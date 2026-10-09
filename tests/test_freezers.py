#!/usr/bin/env python3
"""Freezer readings: the relay carries bsf/freezer/<name>/state into state.json, and the away view shows them
without the sensors' MAC addresses. Run: python3 tests/test_freezers.py"""
import json, os, subprocess, sys, tempfile, importlib.util
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails=0
def check(n,c,d=""):
    global fails; print(("  ok " if c else "  FAIL ")+n+("" if c else "  "+str(d)[:300])); fails+=0 if c else 1
PROBE=r'''
import json,sys,types
try: import paho.mqtt.client
except Exception:
    m=types.ModuleType("paho"); mm=types.ModuleType("paho.mqtt"); c=types.ModuleType("paho.mqtt.client"); c.Client=object
    sys.modules.update({"paho":m,"paho.mqtt":mm,"paho.mqtt.client":c})
import importlib.util
spec=importlib.util.spec_from_file_location("pub", sys.argv[1]); pub=importlib.util.module_from_spec(spec); spec.loader.exec_module(pub)
class M:
    def __init__(s,t,p): s.topic=t; s.payload=p.encode()
import time
now=time.time()
pub.on_message(None,None,M("bsf/freezer/freezer-1/state",json.dumps({"ts":int(now)-30,"sensor":"Freezer 1","mac":"49:23:09:15:16:A2","temp_c":-19.3,"humidity":54.4,"probe":True,"battery":66,"rssi":-88})))
pub.on_message(None,None,M("bsf/freezer/x/state",json.dumps({"ts":int(now),"sensor":"bad","temp_c":"warm"})))
pub.on_message(None,None,M("bsf/freezer/y/state","not json"))
print(json.dumps({"freezers":pub._freezers}))
'''
tmp=tempfile.mkdtemp(); cfg=json.load(open(os.path.join(ROOT,"config.example.json"))); cfg["out_dir"]=tmp
p=os.path.join(tmp,"config.json"); json.dump(cfg,open(p,"w"))
r=subprocess.run([sys.executable,"-c",PROBE,os.path.join(ROOT,"publisher/solar_state_publisher.py")],env=dict(os.environ,BSF_CONFIG=p),capture_output=True,text=True,timeout=60)
assert r.returncode==0, r.stderr[-800:]
fz=json.loads(r.stdout.strip().splitlines()[-1])["freezers"]
check("a freezer reading is kept under its sensor name", list(fz)==["Freezer 1"] and fz["Freezer 1"]["temp_c"]==-19.3 and fz["Freezer 1"]["battery"]==66, fz)
check("the relay never copies the sensor's MAC", "mac" not in fz["Freezer 1"])
check("non-numeric and non-JSON readings are dropped", "bad" not in fz)
spec=importlib.util.spec_from_file_location("srv", os.path.join(ROOT,"publisher/dashboard_server.py")); srv=importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)
pub_state=srv.public_state({"ok":True,"freezers":{"Freezer 1":{"ts":1,"temp_c":-19.3,"mac":"49:23:09:15:16:A2","battery":66}},"presence":{"anyone_home":True}})
check("away view: freezers shown", pub_state.get("freezers",{}).get("Freezer 1",{}).get("temp_c")==-19.3)
check("away view: still no MAC, still no presence", "mac" not in pub_state["freezers"]["Freezer 1"] and "presence" not in pub_state)
print("\n%d failure(s)"%fails); sys.exit(1 if fails else 0)
