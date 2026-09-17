#!/usr/bin/env python3
"""
Dashboard server: the static dashboard + a feedback drop-box.

Replaces `python3 -m http.server` for the per-site dashboard. Everything it served before
it still serves (read-only files out of --dir). It adds exactly two routes:

    POST /feedback        a note from whoever is looking at this dashboard (JSON)
    GET  /feedback.json   every note held on this box, newest first (the fleet relay reads this)

Why on the Pi and not in a cloud: the people leaving notes (the household) are on their
own Wi-Fi looking at their own Pi -- no account, no Tailscale, no internet needed. The note
is written to disk here with the readings at that moment, and the installer's fleet relay
collects it over the tailnet whenever it can. A note left during an internet outage is
not lost; it just arrives later. Same idea as RoadRecord's "a report carries its own
context and survives no signal", without a Firebase project in an open-source bundle.

Notes live OUTSIDE the web root (--feedback-dir) so the static handler can never list or
serve them by path. Nothing here can change dispatcher state: there is no write route
other than appending a note.

PUBLIC MODE (--public --token-file F): the read-only face a tunnel points at, so the household can see
their system away from home. It is deliberately a different, much smaller server:
    - binds 127.0.0.1 only (the tunnel daemon connects locally; nothing on the LAN or internet can reach it direct)
    - GET/HEAD only; every path must start with /<token>/ ; anything else is a plain 404 (never a 401 -- do not
      confirm to a scanner that there is something here)
    - serves a fixed whitelist: the dashboard page, its config, state.json, history.json, weekly.json. No directory
      listings, no feedback routes (notes are left at home, not by the internet), no other file
    - state.json is filtered through a key whitelist: presence, LAN addresses and device ids never leave the house
    - Referrer-Policy: no-referrer, because the token is in the URL and the page links out

Limits (all enforced server-side, the page's own checks are a courtesy):
    body <= 16 KB, text 1..2000 chars, <= 30 notes per rolling hour, <= 500 notes kept.
"""
import argparse, json, os, re, secrets, sys, time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 16 * 1024
MAX_TEXT = 2000
MAX_PER_HOUR = 30
MAX_KEPT = 500
KINDS = ("wrong", "change", "question", "other")
CTX_KEYS = ("site", "page", "soc", "pv_w", "load_w", "batt_w", "hw_state", "ac_state", "ac_mode",
            "curtailed", "stale", "conn", "state_ts", "theme", "viewport", "build")

def clean_context(ctx):
    """Whitelist + flatten: scalars only, bounded length. Pure; unit-tested."""
    out = {}
    if not isinstance(ctx, dict): return out
    for k in CTX_KEYS:
        v = ctx.get(k)
        if v is None: continue
        if isinstance(v, bool) or isinstance(v, (int, float)): out[k] = v
        elif isinstance(v, str): out[k] = v[:200]
        elif isinstance(v, list): out[k] = [str(x)[:40] for x in v[:12]]
    return out

def validate(body):
    """-> (note_dict, None) or (None, 'reason'). Pure; unit-tested."""
    if not isinstance(body, dict): return None, "body must be a JSON object"
    text = body.get("text")
    if not isinstance(text, str) or not text.strip(): return None, "text is required"
    text = text.strip()
    if len(text) > MAX_TEXT: return None, "text is longer than %d characters" % MAX_TEXT
    kind = body.get("kind") if body.get("kind") in KINDS else "other"
    name = body.get("name")
    name = name.strip()[:60] if isinstance(name, str) and name.strip() else None
    return {"text": text, "kind": kind, "name": name, "context": clean_context(body.get("context")),
            "client_ts": body.get("client_ts") if isinstance(body.get("client_ts"), (int, float)) else None}, None

class Store:
    def __init__(self, path):
        self.dir = path; os.makedirs(path, exist_ok=True)
    def _files(self):
        return sorted(f for f in os.listdir(self.dir) if re.fullmatch(r"\d{13}-[0-9a-f]{8}\.json", f))
    def recent_count(self, window_s=3600):
        cutoff = int((time.time() - window_s) * 1000)
        return sum(1 for f in self._files() if int(f[:13]) >= cutoff)
    def add(self, note):
        ts = int(time.time() * 1000); nid = "%013d-%s" % (ts, secrets.token_hex(4))
        note = dict(note, id=nid, received_ts=ts)
        tmp = os.path.join(self.dir, nid + ".tmp")
        with open(tmp, "w") as f: json.dump(note, f, indent=1)
        os.replace(tmp, os.path.join(self.dir, nid + ".json"))
        for old in self._files()[:-MAX_KEPT]: os.remove(os.path.join(self.dir, old))
        return note
    def all(self):
        notes = []
        for f in reversed(self._files()):
            try: notes.append(json.load(open(os.path.join(self.dir, f))))
            except (OSError, ValueError): pass
        return notes

class Handler(SimpleHTTPRequestHandler):
    store = None; site = ""
    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def end_headers(self):
        # live data files must never be cached by a phone browser
        if self.path.split("?")[0].endswith(".json"): self.send_header("Cache-Control", "no-store")
        super().end_headers()
    def do_GET(self):
        if self.path.split("?")[0] == "/feedback.json":
            return self._json(200, {"site": self.site, "ts": int(time.time() * 1000), "notes": self.store.all()})
        return super().do_GET()
    def do_POST(self):
        if self.path.split("?")[0] != "/feedback": return self._json(404, {"ok": False, "error": "no such route"})
        try: n = int(self.headers.get("Content-Length") or 0)
        except ValueError: n = 0
        if n <= 0 or n > MAX_BODY: return self._json(413, {"ok": False, "error": "body must be 1..%d bytes" % MAX_BODY})
        try: body = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError): return self._json(400, {"ok": False, "error": "body is not valid JSON"})
        note, err = validate(body)
        if err: return self._json(400, {"ok": False, "error": err})
        if self.store.recent_count() >= MAX_PER_HOUR:
            return self._json(429, {"ok": False, "error": "too many notes in the last hour, try again later"})
        note["site"] = self.site
        saved = self.store.add(note)
        return self._json(201, {"ok": True, "id": saved["id"]})
    def log_message(self, fmt, *a):   # keep the journal quiet: one line per POST, none per GET
        if self.command == "POST": sys.stderr.write("[dashboard] %s %s\n" % (self.command, fmt % a))

PUBLIC_FILES = {"": "solar_dispatch_dashboard.html", "solar_dispatch_dashboard.html": "solar_dispatch_dashboard.html",
                "dashboard-config.js": "dashboard-config.js", "state.json": "state.json",
                "history.json": "history.json", "weekly.json": "weekly.json"}
PUBLIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".json": "application/json"}
STATE_KEYS = ("ok", "ts", "hw", "ac", "hw_age_s", "ac_age_s", "loads", "weekly", "today", "source")
PRIVATE_SUBKEYS = ("device_id", "local_key", "local_ip", "ip", "mac", "tuya_lan", "chargers_raw")
# Occupancy is the thing that must never leave the house: a timeline of anyone_home is a burglar's calendar.
# Matched as a PATTERN at every depth, in every JSON file served, so a new presence field added upstream is
# stripped by default instead of leaking until someone remembers this list.
PRIVATE_KEY_RE = re.compile(r"presence|anyone_home|home_since|last_seen|roster|occup", re.I)
MIN_TOKEN = 32

def scrub(v):
    """Drop identity-, address- and occupancy-shaped keys at any depth; drop presence events. Pure; unit-tested."""
    if isinstance(v, dict):
        return {k: scrub(x) for k, x in v.items() if k not in PRIVATE_SUBKEYS and not PRIVATE_KEY_RE.search(k)}
    if isinstance(v, list):
        return [scrub(x) for x in v if not (isinstance(x, dict) and PRIVATE_KEY_RE.search(str(x.get("src", "")) + " " + str(x.get("name", "")) + " " + str(x.get("kind", ""))))]
    return v

def public_state(snap):
    """Whitelist the top level of state.json, then scrub below it. Pure; unit-tested."""
    return {k: scrub(snap[k]) for k in STATE_KEYS if isinstance(snap, dict) and k in snap}

def split_token_path(path, token):
    """'/<token>/state.json?x' -> 'state.json'; '/<token>/' -> ''; anything else -> None. Pure; unit-tested.
    The token must be followed by a slash: '/<token>' alone is not a page. Compared in constant time."""
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"): return None
    given, sep, rest = path[1:].partition("/")
    if not sep: return None
    if not secrets.compare_digest(given.encode("utf-8", "replace"), token.encode()): return None
    return rest

class PublicHandler(SimpleHTTPRequestHandler):
    root = ""; token = ""
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                     ("Referrer-Policy", "no-referrer"), ("X-Robots-Tag", "noindex, nofollow"),
                     ("X-Content-Type-Options", "nosniff")): self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(body)
    def _serve(self):
        rest = split_token_path(self.path, self.token)
        name = PUBLIC_FILES.get(rest) if rest is not None else None
        if not name: return self._send(404, b"not found")
        try: data = open(os.path.join(self.root, name), "rb").read()
        except OSError: return self._send(404, b"not found")
        if name == "dashboard-config.js":
            # Tell the page it is the away view (it hides what cannot work from outside, e.g. the feedback box) and
            # blur the site's coordinates to ~10 km: enough for the sun arc, not enough to find the house.
            data += (b"\n;(function(c){if(!c)return;c.publicView=true;"
                     b"if(typeof c.lat==='number')c.lat=Math.round(c.lat*10)/10;"
                     b"if(typeof c.lon==='number')c.lon=Math.round(c.lon*10)/10;})(window.BSF_CONFIG);\n")
        if name.endswith(".json"):          # every data file is filtered, not just the one we thought of first
            try:
                obj = json.loads(data.decode())
                data = json.dumps(public_state(obj) if name == "state.json" else scrub(obj)).encode()
            except ValueError: return self._send(503, b"data not ready")
        self._send(200, data, PUBLIC_TYPES[os.path.splitext(name)[1]])
    def do_GET(self): self._serve()
    def do_HEAD(self): self._serve()
    def _no(self): self._send(404, b"not found")
    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _no
    def log_message(self, fmt, *a): pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="web root (the relay's data dir)")
    ap.add_argument("--feedback-dir", help="where notes are kept; must be OUTSIDE --dir (not used with --public)")
    ap.add_argument("--port", type=int, default=8780); ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--site", default="")
    ap.add_argument("--public", action="store_true", help="read-only, token-gated, localhost-only face for a tunnel")
    ap.add_argument("--token-file", help="--public: file holding the URL token (>= %d chars)" % MIN_TOKEN)
    a = ap.parse_args()
    if a.public:
        try: token = open(a.token_file).read().strip()
        except (OSError, TypeError): sys.exit("--public needs a readable --token-file")
        if len(token) < MIN_TOKEN or not re.fullmatch(r"[A-Za-z0-9_-]+", token): sys.exit("token must be >= %d url-safe characters" % MIN_TOKEN)
        if a.bind not in ("127.0.0.1", "::1", "localhost"): sys.exit("--public only binds to localhost; the tunnel connects locally")
        PublicHandler.root = os.path.realpath(a.dir); PublicHandler.token = token
        srv = ThreadingHTTPServer((a.bind, a.port), PublicHandler)
        print("[dashboard-public] read-only on %s:%d (token-gated)" % (a.bind, a.port), flush=True)
        return srv.serve_forever()
    if not a.feedback_dir: sys.exit("--feedback-dir is required (unless --public)")
    root, fdir = os.path.realpath(a.dir), os.path.realpath(a.feedback_dir)
    if fdir == root or fdir.startswith(root + os.sep): sys.exit("--feedback-dir must not be inside --dir")
    Handler.store = Store(fdir); Handler.site = a.site
    srv = ThreadingHTTPServer((a.bind, a.port), partial(Handler, directory=root))
    print("[dashboard] serving %s on :%d, notes in %s" % (root, a.port, fdir), flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
