#!/usr/bin/env python3
"""Public (tunnel-facing) dashboard server tests: runs a real instance and attacks it. Run: python3 tests/test_public_server.py"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request, urllib.error, importlib.util
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec=importlib.util.spec_from_file_location("ds", os.path.join(ROOT,"publisher/dashboard_server.py")); ds=importlib.util.module_from_spec(spec); spec.loader.exec_module(ds)
fails=0
def check(n,c):
    global fails; print(("  ok " if c else "  FAIL ")+n); fails+=0 if c else 1
T="A"*20+"b"*20
# --- pure ---
check("token + file", ds.split_token_path("/"+T+"/state.json?x=1",T)=="state.json")
check("token + bare slash = index", ds.split_token_path("/"+T+"/",T)=="")
check("token without trailing slash refused", ds.split_token_path("/"+T,T) is None)
check("wrong token refused", ds.split_token_path("/"+"Z"*40+"/state.json",T) is None)
check("token as a prefix of a longer segment refused", ds.split_token_path("/"+T+"x/state.json",T) is None)
check("no token refused", ds.split_token_path("/state.json",T) is None and ds.split_token_path("/",T) is None)
snap={"ok":True,"ts":1,"hw":{"soc":91,"chargers":{"m1":5}},"presence":{"steven":{"home":True}},"presence_age_s":3,
      "tuya_lan":{"hw":{"ip":"192.168.1.37"}},"loads":{"hot_water_w":2400,"device_id":"abc","local_ip":"192.168.1.37","nested":{"local_key":"k","w":1}},"secret_new_field":"x"}
pub=ds.public_state(snap); flat=json.dumps(pub)
check("public state keeps readings", pub["hw"]["soc"]==91 and pub["loads"]["hot_water_w"]==2400 and pub["loads"]["nested"]["w"]==1)
check("presence, LAN IPs, device ids, keys all stripped", all(x not in flat for x in ("presence","steven","192.168","abc","local_key","tuya_lan")))
check("unknown future top-level fields are NOT passed through", "secret_new_field" not in pub)
# The REAL shapes found on a live Pi (2026-09-18): occupancy lives INSIDE the ac block and inside every history sample.
real={"ok":True,"ts":1,"hw":{"soc":81},"ac":{"acState":"off","anyone_home":True,"presence_configured":True,"presence_known":True,"inside_temp":21.5}}
rp=ds.public_state(real)
check("REAL shape: ac readings kept, anyone_home/presence_* stripped from inside ac", rp["ac"]["inside_temp"]==21.5 and rp["ac"]["acState"]=="off" and not any(ds.PRIVATE_KEY_RE.search(k) for k in rp["ac"]))
hist={"samples":[{"ts":1,"hw":{"soc":80},"ac":{"anyone_home":True,"acState":"on"}},{"ts":2,"hw":{"soc":81},"ac":{"anyone_home":False,"home_since_ms":5}}],
      "events":[{"src":"hw","name":"on_surplus"},{"src":"presence","name":"steven arrived"},{"src":"ac","name":"OFF_presence_guard"}],"daily":[{"d":"2026-09-17","solar_kwh":31.7}]}
hs=ds.scrub(hist); hflat=json.dumps(hs)
check("REAL shape: occupancy stripped from EVERY history sample", "anyone_home" not in hflat and "home_since" not in hflat and hs["samples"][0]["ac"]["acState"]=="on" and len(hs["samples"])==2)
check("presence events dropped, dispatch events and daily energy kept", [e["name"] for e in hs["events"]]==["on_surplus"] and hs["daily"][0]["solar_kwh"]==31.7)
check("a NEW presence-ish field added upstream is stripped by default", "last_seen_ms" not in json.dumps(ds.scrub({"x":{"last_seen_ms":1,"roster":["a"],"occupancy":2,"w":3}})) and ds.scrub({"x":{"w":3,"roster":[]}})=={"x":{"w":3}})

# --- live ---
tmp=tempfile.mkdtemp(); web=os.path.join(tmp,"web"); os.makedirs(web)
open(os.path.join(web,"solar_dispatch_dashboard.html"),"w").write("<html>dash</html>")
open(os.path.join(web,"dashboard-config.js"),"w").write("window.BSF_CONFIG={};")
snap["ac"]={"acState":"off","anyone_home":True,"presence_configured":True}
json.dump(snap,open(os.path.join(web,"state.json"),"w")); json.dump(hist,open(os.path.join(web,"history.json"),"w"))
open(os.path.join(web,"secret.txt"),"w").write("nope"); os.makedirs(os.path.join(web,"sub")); open(os.path.join(web,"sub","x.json"),"w").write("{}")
tf=os.path.join(tmp,"token"); open(tf,"w").write(T+"\n")
s=socket.socket(); s.bind(("127.0.0.1",0)); port=s.getsockname()[1]; s.close()
exe=[sys.executable, os.path.join(ROOT,"publisher/dashboard_server.py")]
p=subprocess.Popen(exe+["--dir",web,"--public","--token-file",tf,"--port",str(port),"--bind","127.0.0.1"],stderr=subprocess.DEVNULL,stdout=subprocess.DEVNULL)
base="http://127.0.0.1:%d"%port
def req(path, method="GET", data=None):
    r=urllib.request.Request(base+path, data=data, method=method)
    try:
        with urllib.request.urlopen(r,timeout=5) as x: return x.status, x.read().decode(), x.headers
    except urllib.error.HTTPError as e: return e.code, e.read().decode(), e.headers
for _ in range(50):
    try: req("/"); break
    except Exception: time.sleep(0.1)
try:
    st,b,h=req("/"+T+"/"); check("index with token => dashboard", st==200 and "dash" in b)
    st,b,_=req("/"+T+"/dashboard-config.js"); check("config marks the away view + blurs coordinates", st==200 and "publicView=true" in b and "Math.round(c.lat*10)/10" in b and b.startswith("window.BSF_CONFIG={};"))
    check("no-referrer + noindex + no-store headers", h.get("Referrer-Policy")=="no-referrer" and "noindex" in (h.get("X-Robots-Tag") or "") and h.get("Cache-Control")=="no-store")
    st,b,_=req("/"+T+"/state.json"); d=json.loads(b); check("state.json served FILTERED", st==200 and d["hw"]["soc"]==91 and "presence" not in d and "192.168" not in b and "abc" not in b)
    st,b,_=req("/"+T+"/history.json"); check("history.json served FILTERED over the wire (no occupancy, no presence events)", st==200 and "anyone_home" not in b and "steven" not in b and "on_surplus" in b and "31.7" in b)
    st,b,_=req("/"+T+"/state.json"); check("state.json over the wire carries no occupancy", "anyone_home" not in b and "presence" not in b and "acState" in b)
    for path,label in (("/","root without token"),("/state.json","state without token"),("/"+"Z"*40+"/state.json","wrong token"),
                       ("/"+T,"token without slash"),("/"+T+"/secret.txt","non-whitelisted file"),("/"+T+"/sub/x.json","subdirectory"),
                       ("/"+T+"/../"+T+"/secret.txt","traversal"),("/"+T+"/%2e%2e/secret.txt","encoded traversal"),
                       ("/"+T+"/feedback.json","feedback list"),("/feedback.json","feedback list, no token"),("/"+T+"/sub/","directory listing")):
        st,b,_=req(path); check("%s => 404" % label, st==404 and "nope" not in b)
    for m in ("POST","PUT","DELETE","PATCH"):
        st,_,_=req("/"+T+"/feedback", method=m, data=b'{"text":"x"}'); check("%s => 404 (no write route exists)" % m, st==404)
    st,_,_=req("/"+T+"/state.json", method="HEAD"); check("HEAD allowed", st==200)
    check("state.json on disk untouched", json.load(open(os.path.join(web,"state.json")))==snap)
finally:
    p.terminate(); p.wait(timeout=5)
r=subprocess.run(exe+["--dir",web,"--public","--token-file",tf,"--bind","0.0.0.0"],capture_output=True,text=True,timeout=10)
check("refuses to bind publicly in public mode", r.returncode!=0 and "localhost" in (r.stderr+r.stdout))
open(tf,"w").write("short"); r=subprocess.run(exe+["--dir",web,"--public","--token-file",tf],capture_output=True,text=True,timeout=10)
check("refuses a short token", r.returncode!=0 and "token" in (r.stderr+r.stdout))
r=subprocess.run(exe+["--dir",web,"--public"],capture_output=True,text=True,timeout=10)
check("refuses to start with no token file", r.returncode!=0)
print("\n%d failure(s)"%fails); sys.exit(1 if fails else 0)
