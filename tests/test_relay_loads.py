#!/usr/bin/env python3
"""Relay load attribution: a load with no device must never be given invented watts or energy; a load WITH a device
keeps the 'commanded on + meter quiet => element rating' estimate. Run: python3 tests/test_relay_loads.py"""
import json, os, subprocess, sys, tempfile
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE=r'''
import json,sys,types
try: import paho.mqtt.client
except Exception:
    m=types.ModuleType("paho"); mm=types.ModuleType("paho.mqtt"); c=types.ModuleType("paho.mqtt.client"); c.Client=object
    sys.modules.update({"paho":m,"paho.mqtt":mm,"paho.mqtt.client":c})
import importlib.util
spec=importlib.util.spec_from_file_location("pub", sys.argv[1]); pub=importlib.util.module_from_spec(spec); spec.loader.exec_module(pub)
hw={"ac_load":445,"hwState":"on","soc":98,"surplus_now":2300}
loads=pub.attribute_loads(hw,{"acState":"off"},{})
b=pub._new_bucket() if hasattr(pub,"_new_bucket") else None
print(json.dumps({"connected":pub.HW_CONNECTED,"loads":loads}))
'''
fails=0
def check(n,c):
    global fails; print(("  ok " if c else "  FAIL ")+n); fails+=0 if c else 1
def run(device_id):
    tmp=tempfile.mkdtemp(); cfg=json.load(open(os.path.join(ROOT,"config.example.json")))
    cfg["out_dir"]=tmp; cfg["dispatcher"]["hw_element_w"]=1100
    cfg["loads"]=[{"role":"hot_water","label":"HW","rated_w":1100,"device_id":device_id,"local_key":"","local_ip":""}]
    p=os.path.join(tmp,"config.json"); json.dump(cfg,open(p,"w"))
    r=subprocess.run([sys.executable,"-c",PROBE,os.path.join(ROOT,"publisher/solar_state_publisher.py")],env=dict(os.environ,BSF_CONFIG=p),capture_output=True,text=True,timeout=60)
    assert r.returncode==0, r.stderr[-600:]
    return json.loads(r.stdout.strip().splitlines()[-1])
no=run(""); L=no["loads"]
check("no device: flagged not connected", no["connected"] is False and L["hot_water_connected"] is False and L["hot_water_state"]=="not_connected")
check("no device: NO invented watts even though the dispatcher says on", L["hot_water_w"] is None and L["hot_water_estimated"] is False)
check("no device: real house load is NOT reduced by phantom hot water", L["essential_other_w"]==445 and L["attribution_mismatch"] is False)
check("no device: says it WOULD run now", L["hot_water_would_run"] is True)
yes=run("bf1234567890abcdef"); L=yes["loads"]
check("with a device: the existing estimate is unchanged (element rating, marked estimated)", yes["connected"] is True and L["hot_water_w"]==1100 and L["hot_water_estimated"] is True)
check("with a device: estimate IS subtracted from the house load, as before", L["essential_other_w"]==0 and L["hot_water_would_run"] is False)
print("\n%d failure(s)"%fails); sys.exit(1 if fails else 0)
