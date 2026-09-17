#!/usr/bin/env python3
"""
Fleet relay -- one file for many BSF Solar Dispatch sites.

Polls each site's relay `state.json` (over the LAN or a Tailscale tailnet), keeps
ONLY the fields the fleet page needs, and writes `fleet.json` next to `fleet.html`
so one static server can show every site on one screen. Presence, MACs, Tuya
device ids and anything else personal are never copied -- this file is safe to
serve to a wider audience than the per-site dashboards.

Config (fleet.json is the OUTPUT; the input is sites.json):
    {"poll_s": 15, "sites": [
        {"key":"home",  "name":"Home",   "state_url":"http://127.0.0.1:8780/state.json",
         "dashboard_url":"http://my-mac:8780/solar_dispatch_dashboard.html"},
        {"key":"site-a","name":"Site A", "state_url":"http://site-a-pi:8780/state.json",
         "dashboard_url":"http://site-a-pi:8780/solar_dispatch_dashboard.html"}
    ]}

Usage:  python3 fleet_relay.py [--config sites.json] [--out fleet.json] [--once]
"""
import argparse, json, os, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
STALE_SOFT_S = 180     # feed lagging (the farm feed lags 1-2 min during midday curtailment; do not amber that)
STALE_HARD_S = 600     # site offline for the fleet view

def _num(v):
    try:
        f = float(v); return f if f == f else None
    except (TypeError, ValueError): return None

def summarise(site, snap, fetched_ms):
    """Whitelist copy of a site snapshot -> fleet card. Pure; unit-tested."""
    hw = snap.get("hw") or {}; ac = snap.get("ac") or {}; loads = snap.get("loads") or {}; today = snap.get("today") or {}
    ts = _num(snap.get("ts"))
    age_s = round((fetched_ms - ts) / 1000.0, 1) if ts else None
    hw_age = _num(snap.get("hw_age_s"))
    # The dispatcher lists inputs it has stopped receiving in hw.stale. A site whose core inputs are stale is
    # showing its LAST numbers, not live ones -- say so, and do not pass the numbers off as current.
    dead_inputs = [k for k in (hw.get("stale") or []) if k in ("soc", "pv_dc", "pv_fronius", "ac_load")]
    stale = "ok"
    if dead_inputs:
        return dict(offline(site, "no live readings from the inverter (%s)" % ", ".join(dead_inputs)), age_s=age_s)
    if not snap.get("ok") or age_s is None or age_s > STALE_HARD_S or (hw_age is not None and hw_age > STALE_HARD_S): stale = "offline"
    elif age_s > STALE_SOFT_S or (hw_age is not None and hw_age > STALE_SOFT_S): stale = "lagging"
    return {
        "key": site["key"], "name": site.get("name") or site["key"],
        "dashboard_url": site.get("dashboard_url") or "",
        "ok": bool(snap.get("ok")), "state": stale, "age_s": age_s, "hw_age_s": hw_age,
        "soc": _num(hw.get("soc")), "pv_w": _num(hw.get("pv_total")), "load_w": _num(hw.get("ac_load")),
        "batt_w": _num(hw.get("batt_power")), "curtailed": bool(hw.get("curtailed")),
        "hw_state": hw.get("hwState"), "hw_w": _num(loads.get("hot_water_w")),
        "ac_state": ac.get("acState"), "ac_mode": ac.get("ac_mode"), "inside_temp": _num(ac.get("inside_temp")),
        "today_hw_kwh": _num(today.get("hw_kwh")), "today_ac_kwh": _num(today.get("ac_kwh")), "today_hw_h": _num(today.get("hw_h")),
        "source": snap.get("source"),
    }

def _friendly(err):
    t = str(err)
    if "timed out" in t: return "no response (site off, or not on the tailnet)"
    if "refused" in t: return "site up but its relay isn't serving"
    if "Name or service" in t or "nodename" in t: return "hostname unknown (Tailscale off on this machine?)"
    return t[:100]

def offline(site, err):
    return {"key": site["key"], "name": site.get("name") or site["key"], "dashboard_url": site.get("dashboard_url") or "",
            "ok": False, "state": "offline", "age_s": None, "error": _friendly(err),
            "soc": None, "pv_w": None, "load_w": None, "batt_w": None, "curtailed": False,
            "hw_state": None, "hw_w": None, "ac_state": None, "ac_mode": None, "inside_temp": None,
            "today_hw_kwh": None, "today_ac_kwh": None, "today_hw_h": None, "source": None}

def fetch(site, timeout):
    now_ms = int(time.time() * 1000)
    try:
        with urllib.request.urlopen(urllib.request.Request(site["state_url"], headers={"Cache-Control": "no-cache"}), timeout=timeout) as r:
            return summarise(site, json.loads(r.read().decode()), now_ms)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return offline(site, e)

# ---- feedback inbox -----------------------------------------------------------------------
# Each Pi's dashboard server holds the household's notes (publisher/dashboard_server.py). We copy them here so
# the installer reads one inbox, and so a note outlives a wiped or offline Pi. Notes are UNTRUSTED input -- at a
# site with open Wi-Fi anyone in range can post one -- so every field is re-whitelisted and bounded on the way in,
# and fleet.html only ever renders them as text.
NOTE_CTX = ("soc", "pv_w", "load_w", "batt_w", "hw_state", "ac_state", "ac_mode", "curtailed", "stale", "conn", "theme", "viewport", "build", "page")
FEEDBACK_EVERY_S = 60
MAX_INBOX = 1000

def clean_note(site, n):
    """Pure; unit-tested. -> note dict or None."""
    if not isinstance(n, dict): return None
    nid, text = n.get("id"), n.get("text")
    if not (isinstance(nid, str) and 0 < len(nid) <= 40 and isinstance(text, str) and text.strip()): return None
    ctx = n.get("context") if isinstance(n.get("context"), dict) else {}
    safe = {}
    for k in NOTE_CTX:
        v = ctx.get(k)
        if isinstance(v, bool) or isinstance(v, (int, float)): safe[k] = v
        elif isinstance(v, str): safe[k] = v[:120]
        elif isinstance(v, list): safe[k] = [str(x)[:40] for x in v[:12]]
    return {"key": "%s/%s" % (site["key"], nid), "site_key": site["key"], "site": site.get("name") or site["key"],
            "id": nid, "kind": n.get("kind") if n.get("kind") in ("wrong", "change", "question", "other") else "other",
            "name": n["name"][:60] if isinstance(n.get("name"), str) else None, "text": text.strip()[:2000],
            "received_ts": _num(n.get("received_ts")), "context": safe}

def feedback_url(site):
    u = site.get("feedback_url")
    if u: return u
    su = site["state_url"]
    return su[:-len("state.json")] + "feedback.json" if su.endswith("/state.json") else None

def merge_inbox(inbox, site, payload):
    """Add unseen notes from one site's /feedback.json. Returns how many were new. Pure; unit-tested."""
    have = {n["key"] for n in inbox}; added = 0
    for raw in (payload.get("notes") if isinstance(payload, dict) else None) or []:
        n = clean_note(site, raw)
        if n and n["key"] not in have: inbox.append(n); have.add(n["key"]); added += 1
    inbox.sort(key=lambda n: n.get("received_ts") or 0, reverse=True)
    del inbox[MAX_INBOX:]
    return added

def collect_feedback(sites, inbox_path, timeout):
    try: inbox = json.load(open(inbox_path)).get("notes", [])
    except (OSError, ValueError): inbox = []
    added = 0
    for site in sites:
        url = feedback_url(site)
        if not url: continue
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r: added += merge_inbox(inbox, site, json.loads(r.read().decode()))
        except (urllib.error.URLError, OSError, ValueError): pass      # site offline, or not a box that takes notes
    write_atomic(inbox_path, {"ts": int(time.time() * 1000), "notes": inbox})
    return added, len(inbox)

def write_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f, indent=1)
    os.replace(tmp, path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "sites.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "fleet.json"))
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    cfg = json.load(open(a.config))
    sites = [s for s in cfg.get("sites", []) if s.get("key") and s.get("state_url")]
    if not sites: print("[fleet] no sites in %s" % a.config, flush=True); sys.exit(2)
    poll = float(cfg.get("poll_s", 15)); timeout = float(cfg.get("timeout_s", 6))
    print("[fleet] %d site(s) every %ss -> %s" % (len(sites), poll, a.out), flush=True)
    inbox_path = os.path.join(os.path.dirname(os.path.abspath(a.out)), "fleet_feedback.json"); last_fb = 0
    while True:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=max(1, len(sites))) as ex:
            cards = list(ex.map(lambda s: fetch(s, timeout), sites))
        write_atomic(a.out, {"ts": int(time.time() * 1000), "poll_s": poll, "sites": cards})
        print("[fleet] " + "  ".join("%s:%s soc=%s pv=%s" % (c["key"], c["state"], c["soc"], c["pv_w"]) for c in cards), flush=True)
        if a.once or time.time() - last_fb >= FEEDBACK_EVERY_S:
            last_fb = time.time(); added, total = collect_feedback(sites, inbox_path, timeout)
            if added: print("[fleet] feedback: %d new note(s), %d in the inbox" % (added, total), flush=True)
        if a.once: break
        time.sleep(max(1.0, poll - (time.time() - t0)))

if __name__ == "__main__":
    main()
