#!/usr/bin/env python3
"""Selectronic adapter tests: bridge translation + deploy.py node swap. Run: python3 tests/test_selectronic.py"""
import json, os, sys, importlib.util
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def load(name, rel):
    spec=importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel)); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
bridge=load("bridge","publisher/selectlive_bridge.py")
sys.argv=["deploy.py","check"]; deploy=load("deploy","install/deploy.py")
SEL=dict(bridge.DEFAULTS)
fails=0
def check(name, cond):
    global fails
    print(("  ok " if cond else "  FAIL ")+name); fails+= (0 if cond else 1)

pt=json.load(open(os.path.join(ROOT,"tests/selectlive_point.json")))
v=bridge.translate(pt, SEL)
check("soc/ac_load/batt/pv mapped", v["soc"]==97.5 and v["ac_load"]==850 and v["batt_power"]==120 and v["pv_fronius"]==1010 and v["pv_dc"]==0)
check("full battery + sun + idle battery => curtailed (mode 1)", v["mode_288"]==1)
low=json.loads(json.dumps(pt)); low["items"]["battery_soc"]=80
check("SOC 80 => not curtailed", bridge.translate(low,SEL)["mode_288"]==2)
chg=json.loads(json.dumps(pt)); chg["items"]["battery_w"]=1500
check("battery charging hard => not curtailed", bridge.translate(chg,SEL)["mode_288"]==2)
night=json.loads(json.dumps(pt)); night["items"]["solarinverter_w"]=0
check("no sun => not curtailed (never asserts at night)", bridge.translate(night,SEL)["mode_288"]==2)
inv=dict(SEL, invert_battery_sign=True)
check("invert_battery_sign flips battery_w", bridge.translate(pt,inv)["batt_power"]==-120)
bad=bridge.translate({"items":{"battery_soc":"nan","load_w":None}}, SEL)
check("garbage readings are dropped, not published as numbers", "soc" not in bad and "ac_load" not in bad and bad["mode_288"]==2)
check("no ip/device_id => no url", bridge.point_url({"ip":"","device_id":""}) is None)
check("url shape", bridge.point_url({"ip":"10.0.0.5","device_id":"ABC"})=="http://10.0.0.5/cgi-bin/solarmonweb/devices/ABC/point")

flows=json.load(open(os.path.join(ROOT,"node-red/bsf-solar-dispatch.flow.json")))
out,swapped=deploy.selectronic_transform(flows)
check("no victron nodes remain", not any(n["type"].startswith("victron") for n in out))
check("every victron input became an mqtt in on the same id", all(any(o["id"]==n["id"] and o["type"]=="mqtt in" for o in out) for n in flows if n["type"].startswith("victron-input")))
wires_before={n["id"]:n.get("wires") for n in flows if n["type"].startswith("victron-input")}
check("wires preserved", all(next(o for o in out if o["id"]==i)["wires"]==w for i,w in wires_before.items()))
check("dispatcher inputs on bridge topics", {t for _,t in swapped} >= {"sel/soc","sel/pv_dc","sel/pv_fronius","sel/ac_load","sel/batt_power","sel/mode_288"})
check("mqtt ins parse JSON (numbers, not strings)", all(o["datatype"]=="json" for o in out if o["type"]=="mqtt in" and o["topic"].startswith("sel/")))
check("node count = original minus the victron-client", len(out)==len(flows)-1)
cfg={"hardware":{"inverter":{"kind":"selectronic"},"selectronic":{"mqtt_host":"127.0.0.1"}}}
check("selectronic default Node-RED url is local http", deploy._nodered_url(cfg)=="http://127.0.0.1:1880")
check("victron default Node-RED url is the Cerbo", deploy._nodered_url({"hardware":{"cerbo_ip":"1.2.3.4"}})=="https://1.2.3.4:1881")
check("broker token follows selectronic mqtt_host", deploy.token_map(cfg)["__CERBO_IP__"]=="127.0.0.1")
print("\n%d failure(s)" % fails); sys.exit(1 if fails else 0)
