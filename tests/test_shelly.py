#!/usr/bin/env python3
"""Shelly hot-water driver: config checks, the deploy.py node swap, the adapter's two function nodes (run in node),
and the relay's reading of the Shelly topics. Run: python3 tests/test_shelly.py"""
import json, os, shutil, subprocess, sys, tempfile, importlib.util
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def load(name, rel):
    spec=importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel)); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
sys.argv=["deploy.py","check"]; deploy=load("deploy","install/deploy.py")
fails=0
def check(name, cond):
    global fails
    print(("  ok " if cond else "  FAIL ")+name); fails+=(0 if cond else 1)

HW={"role":"hot_water","label":"Booster 1","driver":"shelly","rated_w":3600,"metered":True,
    "shelly":{"topic_prefix":"glen-eden/booster1","switch_id":0,"deadman_s":300}}
def cfg_with(*loads):
    c=json.load(open(os.path.join(ROOT,"config.example.json"))); c["loads"]=[json.loads(json.dumps(l)) for l in loads]; return c
def issues(c): return deploy.validate(c)[0]

# ---- config checks ----
check("a well-formed Shelly hot water has no blockers", not issues(cfg_with(HW)))
check("a Tuya load with no driver field is still Tuya", deploy._driver({"role":"hot_water"})=="tuya")
bad=dict(HW, shelly=dict(HW["shelly"], topic_prefix="shelly/#"))
check("a wildcard topic prefix is a blocker", any("topic_prefix" in i for i in issues(cfg_with(bad))))
check("a blank topic prefix is a blocker", any("topic_prefix" in i for i in issues(cfg_with(dict(HW, shelly={})))))
check("Shelly on the air-con is a blocker (hot water only today)", any("only the hot_water" in i for i in issues(cfg_with(dict(HW, role="air_con")))))
check("a dead-man shorter than 120 s is a blocker", any("deadman_s" in i for i in issues(cfg_with(dict(HW, shelly=dict(HW["shelly"], deadman_s=30))))))
check("no rated_w is a blocker (it stands in for the meter)", any("rated_w" in i for i in issues(cfg_with(dict(HW, rated_w=0)))))
check("an unknown driver is a blocker", any("driver" in i for i in issues(cfg_with(dict(HW, driver="zigbee")))))
check("connected: Shelly with a prefix and no device_id", deploy._connected(HW) and not deploy._connected(dict(HW, shelly={})))

# ---- node swap ----
flows=json.load(open(os.path.join(ROOT,"node-red/bsf-solar-dispatch.flow.json")))
out,dropped=deploy.shelly_transform(json.loads(json.dumps(flows)), HW)
ids={n["id"] for n in out}
check("the Tuya hot-water node and the link-in feeding it are gone", dropped==["8309d2935a2e2894","hot-water-tuya"] and not (ids & set(dropped)))
lo=next(n for n in out if n["id"]=="2e16c9d59bfa60f3")
check("the dispatcher/lockout link-out now reaches the Shelly adapter", "shelly.hw.linkin" in lo["links"] and not set(lo["links"]) & set(dropped))
li=next(n for n in out if n["id"]=="shelly.hw.linkin")
check("the adapter link-in points back at that link-out", li["links"]==["2e16c9d59bfa60f3"])
check("dispatcher and lockout both still feed the link-out", all("2e16c9d59bfa60f3" in n["wires"][0] for n in out if n["id"] in ("hwv3.fn.dispatcher","hwv3.fn.lockout")))
raw=json.dumps([n for n in out if n.get("z")=="shelly.hw.tab"])
check("no Shelly tokens left", "__SHELLY_" not in raw)
check("every adapter node is on the adapter tab and every wire stays on it", all(w in ids for n in out if n.get("z")=="shelly.hw.tab" for ws in n.get("wires",[]) for w in ws))
broker=next(n["id"] for n in flows if n["type"]=="mqtt-broker")
check("adapter MQTT nodes use the flow's own broker", all(n.get("broker")==broker for n in out if n.get("z")=="shelly.hw.tab" and n["type"].startswith("mqtt")))
topics={n["topic"] for n in out if n.get("z")=="shelly.hw.tab" and n["type"]=="mqtt in"}
check("listens on the reply and event topics", topics=={"bsf-dispatch-glen-eden-booster1/rpc","glen-eden/booster1/events/rpc"})
check("a Tuya hot water leaves the flow untouched", len(deploy.build_flow(cfg_with(dict(HW, driver="tuya", device_id="bf1")))[1])==len(flows))
sel=cfg_with(HW); sel["hardware"]["inverter"]["kind"]="selectronic"
_,built,left=deploy.build_flow(sel)
check("Selectronic + Shelly together: no victron nodes, adapter present", not any(n["type"].startswith("victron") for n in built) and any(n["id"]=="shelly.hw.cmd" for n in built))
bad_js=deploy._node_check(built)
check("every function body in the built flow passes node --check", bad_js==[] or bad_js is None)

# ---- the two adapter functions, executed ----
node=shutil.which("node")
fn={n["id"]:n["func"] for n in out if n["type"]=="function"}
HARNESS=r'''
const fs=require('fs'); const cases=JSON.parse(fs.readFileSync(0,'utf8')); const res=[];
for (const c of cases) {
  const store={}; const context={get:k=>store[k], set:(k,v)=>{store[k]=v;}};
  const node={status(){}, warn(){}};
  const f=new Function('msg','context','node','flow',c.func);
  res.push(c.msgs.map(m=>f(m,context,node,{get(){},set(){}})));
}
process.stdout.write(JSON.stringify(res));'''
def run_js(cases):
    r=subprocess.run([node,"-e",HARNESS],input=json.dumps(cases),capture_output=True,text=True,timeout=30)
    assert r.returncode==0, r.stderr[-800:]
    return json.loads(r.stdout)
if not node:
    print("  ! node not installed — skipped executing the adapter functions"); fails+=1
else:
    ON={"payload":{"dps":"1","set":True}}; OFF={"payload":{"dps":"1","set":False}}; TICK={"topic":"tick"}
    cmd=lambda *m: {"func":fn["shelly.hw.cmd"],"msgs":list(m)}
    r=run_js([cmd({"topic":"init"}), cmd(ON, TICK), cmd(ON, OFF, TICK), cmd(TICK)])
    rpc=lambda m: json.loads(m["payload"])
    init=r[0][0][0]
    check("init: power-on state OFF, then a status read", [rpc(m)["method"] for m in init]==["Switch.SetConfig","Switch.GetStatus"] and rpc(init[0])["params"]=={"id":0,"config":{"initial_state":"off"}})
    on=r[1][0][0]
    check("ON: Switch.Set on with the dead-man, to <prefix>/rpc, replies to our src", on[0]["topic"]=="glen-eden/booster1/rpc" and rpc(on[0])["params"]=={"id":0,"on":True,"toggle_after":300} and rpc(on[0])["src"]=="bsf-dispatch-glen-eden-booster1")
    check("tick while ON: status read + dead-man refreshed", [ (rpc(m)["method"], rpc(m)["params"].get("on")) for m in r[1][1][0]]==[("Switch.GetStatus",None),("Switch.Set",True)])
    check("OFF: Switch.Set off, no timer", rpc(r[2][1][0][0])["params"]=={"id":0,"on":False})
    check("tick after OFF: status read only, OFF is not re-asserted", [rpc(m)["method"] for m in r[2][2][0]]==["Switch.GetStatus"])
    check("tick before any command: status read only", [rpc(m)["method"] for m in r[3][0][0]]==["Switch.GetStatus"])
    check("RPC ids increase per message", len({rpc(m)["id"] for m in r[1][0][0]+r[1][1][0]})==4)

    def st(metered, *msgs):
        f=fn["shelly.hw.status"].replace("const METERED    = true","const METERED    = %s" % ("true" if metered else "false"))
        return {"func":f,"msgs":[{"payload":json.dumps(m)} for m in msgs]}
    reply=lambda out,ap=None: {"id":3,"src":"glen-eden/booster1","dst":"x","result":dict({"id":0,"output":out},**({"apower":ap} if ap is not None else {}))}
    notify=lambda d: {"src":"glen-eden/booster1","method":"NotifyStatus","params":{"ts":1,"switch:0":dict({"id":0},**d)}}
    r=run_js([st(True, reply(True,3512.6)), st(False, reply(True,3512.6)), st(True, reply(False,0)), st(True, reply(True)),
              st(True, notify({"apower":12}), reply(True,3500), notify({"apower":0.4}), notify({"output":False})),
              st(True, {"id":1,"src":"x","error":{"code":-103,"message":"bad"}}, {"id":2,"result":{"was_on":False}}, "not json")])
    pair=lambda o: (o[0]["topic"],o[0]["payload"],o[1]["topic"],o[1]["payload"])
    check("metered + ON: the relay's own watts", pair(r[0][0])==("bsf/hotwater/plug_state",True,"bsf/hotwater/plug_power",3513))
    check("unmetered + ON: the element rating", r[1][0][1]["payload"]==3600)
    check("OFF: 0 W", r[2][0][0]["payload"] is False and r[2][0][1]["payload"]==0)
    check("metered relay that sends no apower: falls back to the rating", r[3][0][1]["payload"]==3600)
    check("a power-only event before the switch state is known publishes nothing", r[4][0] is None)
    check("events update watts on the known state, then OFF goes to 0", r[4][1][1]["payload"]==3500 and r[4][2][1]["payload"]==0 and r[4][3][0]["payload"] is False and r[4][3][1]["payload"]==0)
    check("RPC errors, Switch.Set acks and garbage publish nothing", r[5]==[None,None,None])

# ---- relay (publisher) ----
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
r={"connected":pub.HW_CONNECTED,"plugs":pub.PLUGS,"none":pub.shelly_plugs(1000)}
pub.on_message(None,None,M("bsf/hotwater/plug_state","true")); pub.on_message(None,None,M("bsf/hotwater/plug_power","3513"))
t=pub.now_ms(); r["on"]=pub.shelly_plugs(t)["hw"]; r["stale"]=pub.shelly_plugs(t+200000)["hw"]
r["loads_on"]=pub.attribute_loads({"ac_load":4100,"hwState":"on"},{},pub.shelly_plugs(t))
pub.on_message(None,None,M("bsf/hotwater/plug_state","false")); r["off"]=pub.shelly_plugs(pub.now_ms())["hw"]
r["loads_stale"]=pub.attribute_loads({"ac_load":4100,"hwState":"on"},{},pub.shelly_plugs(t+200000))
print(json.dumps(r))
'''
def relay(load):
    tmp=tempfile.mkdtemp(); c=cfg_with(load); c["out_dir"]=tmp; c["dispatcher"]["hw_element_w"]=3600
    p=os.path.join(tmp,"config.json"); json.dump(c,open(p,"w"))
    r=subprocess.run([sys.executable,"-c",PROBE,os.path.join(ROOT,"publisher/solar_state_publisher.py")],env=dict(os.environ,BSF_CONFIG=p),capture_output=True,text=True,timeout=60)
    assert r.returncode==0, r.stderr[-800:]
    return json.loads(r.stdout.strip().splitlines()[-1])
z=relay(HW)
check("relay: a Shelly hot water counts as connected, with no Tuya device id", z["connected"] is True)
check("relay: the Shelly is not polled through the Tuya cloud", "hw" not in z["plugs"])
check("relay: nothing heard yet => stale, not live", z["none"]["hw"]["state"]=="stale" and z["none"]["hw"]["metered"] is False)
check("relay: ON + metered => live watts", z["on"]=={"w":3513,"state":"on","on":True,"age_s":0.0,"metered":True} or (z["on"]["w"]==3513 and z["on"]["metered"] is True and z["on"]["state"]=="on"))
check("relay: ON subtracts the relay's watts from the house load", z["loads_on"]["hot_water_w"]==3513 and z["loads_on"]["essential_other_w"]==587)
check("relay: older than 2 min => stale", z["stale"]["state"]=="stale" and z["stale"]["w"] is None)
check("relay: stale while commanded on => element-rating estimate, marked estimated", z["loads_stale"]["hot_water_w"]==3600 and z["loads_stale"]["hot_water_estimated"] is True)
check("relay: OFF => 0 W, real reading", z["off"]["state"]=="off" and z["off"]["w"]==0 and z["off"]["metered"] is True)
u=relay(dict(HW, metered=False));
check("relay: unmetered relay ON => no fake 'live' watts (the rating is an estimate)", u["on"]["metered"] is False and u["on"]["w"] is None)

print("\n%d failure(s)" % fails); sys.exit(1 if fails else 0)
