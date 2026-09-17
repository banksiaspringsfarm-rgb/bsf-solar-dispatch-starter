#!/usr/bin/env python3
"""Dashboard server tests: runs a real instance. Run: python3 tests/test_feedback_server.py"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request, urllib.error, importlib.util
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec=importlib.util.spec_from_file_location("ds", os.path.join(ROOT,"publisher/dashboard_server.py")); ds=importlib.util.module_from_spec(spec); spec.loader.exec_module(ds)
fails=0
def check(n,c):
    global fails; print(("  ok " if c else "  FAIL ")+n); fails+=0 if c else 1

# --- pure ---
n,e=ds.validate({"text":"  hot water is cold  ","kind":"wrong","name":" Trish ","context":{"soc":91.5,"site":"X","evil":{"a":1},"stale":["soc"]*40}})
check("valid note trimmed, kind kept, name trimmed", e is None and n["text"]=="hot water is cold" and n["kind"]=="wrong" and n["name"]=="Trish")
check("context whitelisted: unknown keys dropped, lists capped", "evil" not in n["context"] and n["context"]["soc"]==91.5 and len(n["context"]["stale"])==12)
check("empty text refused", ds.validate({"text":"   "})[1] is not None)
check("over-long text refused", ds.validate({"text":"x"*(ds.MAX_TEXT+1)})[1] is not None)
check("unknown kind => other", ds.validate({"text":"hi","kind":"<script>"})[0]["kind"]=="other")
check("non-object body refused", ds.validate(["text"])[1] is not None)

# --- live server ---
tmp=tempfile.mkdtemp(); web=os.path.join(tmp,"web"); fb=os.path.join(tmp,"notes"); os.makedirs(web)
open(os.path.join(web,"state.json"),"w").write('{"ok":true}')
s=socket.socket(); s.bind(("127.0.0.1",0)); port=s.getsockname()[1]; s.close()
p=subprocess.Popen([sys.executable, os.path.join(ROOT,"publisher/dashboard_server.py"),"--dir",web,"--feedback-dir",fb,"--port",str(port),"--bind","127.0.0.1","--site","Test Site"], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
base="http://127.0.0.1:%d"%port
for _ in range(50):
    try: urllib.request.urlopen(base+"/state.json",timeout=1); break
    except Exception: time.sleep(0.1)
def req(path, data=None, raw=None, method=None):
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    r=urllib.request.Request(base+path, data=body, method=method or ("POST" if body is not None else "GET"), headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(r,timeout=5) as x: return x.status, x.read().decode(), x.headers
    except urllib.error.HTTPError as e: return e.code, e.read().decode(), e.headers
try:
    st,b,h=req("/state.json"); check("static file still served, uncached", st==200 and json.loads(b)["ok"] and h.get("Cache-Control")=="no-store")
    st,b,_=req("/feedback",{"text":"Please make the hot water come on earlier","kind":"change","name":"Mum","context":{"soc":88,"site":"spoofed"}})
    nid=json.loads(b).get("id"); check("POST valid note => 201 + id", st==201 and nid)
    files=os.listdir(fb); check("note written OUTSIDE the web root", len(files)==1 and files[0]==nid+".json" and not os.path.exists(os.path.join(web,"notes")))
    saved=json.load(open(os.path.join(fb,files[0]))); check("server stamps site + received_ts itself", saved["site"]=="Test Site" and saved["received_ts"]>0 and saved["name"]=="Mum")
    st,b,_=req("/feedback.json"); d=json.loads(b); check("GET /feedback.json lists it", st==200 and d["site"]=="Test Site" and len(d["notes"])==1 and d["notes"][0]["id"]==nid)
    st,_,_=req("/feedback",{"text":""}); check("empty text => 400", st==400)
    st,_,_=req("/feedback",raw=b"{not json"); check("bad JSON => 400", st==400)
    st,_,_=req("/feedback",raw=b'{"text":"'+b"x"*(ds.MAX_BODY)+b'"}'); check("oversize body => 413", st==413)
    st,_,_=req("/state.json",{"text":"x"}); check("POST anywhere else => 404, state.json untouched", st==404 and json.load(open(os.path.join(web,"state.json")))=={"ok":True})
    st,_,_=req("/../notes/"+nid+".json"); check("note not reachable by path traversal", st in (400,404))
    st,_,_=req("/notes/"+nid+".json"); check("note not reachable under the web root", st==404)
    for i in range(ds.MAX_PER_HOUR-1): req("/feedback",{"text":"n%d"%i})
    st,_,_=req("/feedback",{"text":"one too many"}); check("rate limit => 429 after %d/hour"%ds.MAX_PER_HOUR, st==429)
    st,_,_=req("/feedback",method="PUT",raw=b"{}"); check("PUT not supported", st in (405,501))
finally:
    p.terminate(); p.wait(timeout=5)
r=subprocess.run([sys.executable, os.path.join(ROOT,"publisher/dashboard_server.py"),"--dir",web,"--feedback-dir",os.path.join(web,"inside")],capture_output=True,text=True,timeout=10)
check("refuses a feedback dir inside the web root", r.returncode!=0 and "must not be inside" in (r.stderr+r.stdout))
print("\n%d failure(s)"%fails); sys.exit(1 if fails else 0)
