#!/usr/bin/env python3
"""Shelly hot-water driver: config checks, the deploy.py node swap, the adapter's two function nodes (run in node),
and the relay's reading of the Shelly topics. Run: python3 tests/test_shelly.py"""
import json, os, shutil, subprocess, sys, tempfile, importlib.util
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def load(name, rel):
    spec=importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel)); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
sys.argv=["deploy.py","check"]; deploy=load("deploy","install/deploy.py")
fails=0
def check(name, cond, detail=""):
    global fails
    print(("  ok " if cond else "  FAIL ")+name+("" if cond or detail=="" else "  -> "+str(detail)[:300])); fails+=(0 if cond else 1)

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
check("Shelly on the air-con is a blocker (hot water only today)", any("only hot_water" in i for i in issues(cfg_with(dict(HW, role="air_con")))))
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

# ---- Booster 2 (secondary) ----
TWO=cfg_with(HW, dict(HW, role="hot_water_2", label="Booster 2", shelly=dict(HW["shelly"], topic_prefix="glen-eden/booster2")))
TWO["dispatcher"].update({"safety_cap_w":11000, "hot_water_2":{"max_total_w":10000}})
check("two Shelly boosters with sane caps: no blockers", not issues(TWO))
t2=json.loads(json.dumps(TWO)); t2["dispatcher"]["safety_cap_w"]=9000
check("safety cap not above Booster 2's total-load limit is a blocker (Booster 2 must shed first)", any("must be > dispatcher.hot_water_2.max_total_w" in i for i in issues(t2)))
t2=json.loads(json.dumps(TWO)); t2["loads"][1]["shelly"]["topic_prefix"]="glen-eden/booster1"
check("both boosters on one prefix is a blocker", any("same shelly.topic_prefix" in i for i in issues(t2)))
t2=json.loads(json.dumps(TWO)); t2["loads"][0]=dict(t2["loads"][0], driver="tuya")
check("Booster 2 on a Shelly needs Booster 1 on a Shelly", any("Booster 1" in i for i in issues(t2)))
t2=json.loads(json.dumps(TWO)); del t2["dispatcher"]["hot_water_2"]; t2["dispatcher"]["safety_cap_w"]=11000
check("default total-load limit = inverter rating, and it warns that Booster 2 will rarely run", deploy._hw2_cfg(t2)["max_total_w"]==5000 and any("rarely" in w for w in deploy.validate(t2)[1]))
two,_=deploy.hot_water_2_transform(json.loads(json.dumps(flows)), TWO)
ids2={n["id"] for n in two}
check("two boosters: each adapter tab has its own ids", {"shelly.hw.cmd","shelly.hw2.cmd","hw2.fn","hw2.combine"}<=ids2 and len(ids2)==len(two))
check("Booster 1 reports on b1 topics, Booster 2 on b2 topics",
      "bsf/hotwater/b1/plug_state" in next(n for n in two if n["id"]=="shelly.hw.status")["func"]
      and "bsf/hotwater/b2/plug_state" in next(n for n in two if n["id"]=="shelly.hw2.status")["func"])
check("the dispatcher/lockout link-out reaches Booster 1 only", next(n for n in two if n["id"]=="2e16c9d59bfa60f3")["links"]==["b3ca21a4de4a338b","shelly.hw.linkin"])
check("the secondary dispatcher reaches Booster 2 only", next(n for n in two if n["id"]=="hw2.linkout")["links"]==["shelly.hw2.linkin"] and next(n for n in two if n["id"]=="shelly.hw2.linkin")["links"]==["hw2.linkout"])
check("every MQTT subscription is a valid topic filter (wildcards fill a whole level)",
      all(all(seg in ("+","#") or ("+" not in seg and "#" not in seg) for seg in n["topic"].split("/")) for n in two if n["type"]=="mqtt in"))
check("Booster 2 adapter listens on its own prefix", {n["topic"] for n in two if n.get("z")=="shelly.hw2.tab" and n["type"]=="mqtt in"}=={"bsf-dispatch-glen-eden-booster2/rpc","glen-eden/booster2/events/rpc"})
check("two-booster flow: every function passes node --check", deploy._node_check(two) in ([],None))
fn2={n["id"]:n["func"] for n in two if n["type"]=="function"}
SIM=r'''
// fake clock + shared flow context; steps: [{at, msg}] -> outputs per step
const fs=require('fs'); const cases=JSON.parse(fs.readFileSync(0,'utf8')); const res=[];
for (const c of cases) {
  let T=1e12; class D extends Date { constructor(...a){ a.length?super(...a):super(T); } static now(){ return T; } }
  const fstore={}, flow={get:k=>fstore[k], set:(k,v)=>{fstore[k]=v;}};
  const ctx={}; const mk=id=>{ctx[id]=ctx[id]||{}; const s=ctx[id]; return {get:k=>s[k], set:(k,v)=>{s[k]=v;}}; };
  const fns={}; for (const [id,src] of Object.entries(c.funcs)) fns[id]=new Function('msg','context','node','flow','Date',src);
  const out=[];
  for (const st of c.steps) { T=1e12+st.at; const r=fns[st.fn](st.msg, mk(st.fn), {status(){},warn(){}}, flow, D); out.push(r); }
  res.push(out);
}
process.stdout.write(JSON.stringify(res));'''
def sim(cases):
    r=subprocess.run([node,"-e",SIM],input=json.dumps(cases),capture_output=True,text=True,timeout=30)
    assert r.returncode==0, r.stderr[-800:]
    return json.loads(r.stdout)
if node:
    F={"c":fn2["hw2.combine"],"s":fn2["hw2.fn"]}
    def plug(at,b,on,w): return [{"at":at,"fn":"c","msg":{"topic":"bsf/hotwater/%s/plug_state"%b,"payload":"true" if on else "false"}},
                                  {"at":at,"fn":"c","msg":{"topic":"bsf/hotwater/%s/plug_power"%b,"payload":str(w)}}]
    def status(at, **kw):
        s=dict(mode="DISPATCH",hwState="on",soc=100,surplus_now=11400,ac_load=4150,batt_power=100,curtailed=False,stale=[]); s.update(kw)
        return {"at":at,"fn":"s","msg":{"topic":"bsf/hotwater/status","payload":json.dumps(s)}}
    def cmds(rs): return [r[0]["payload"]["set"] for r in rs if r and isinstance(r[0], dict)]
    def ticks(t0, n, b2=False, **kw):   # dispatcher status every 10 s; both relays report every 30 s, like the adapters
        out=[]
        for i in range(n):
            at=t0+i*10000
            if i%3==0: out+=plug(at,"b1",True,3550)+plug(at,"b2",b2,3500 if b2 else 0)
            out.append(status(at, **kw))
        return out
    # Booster 1 on with 3550 W from t=0; good sun. Booster 2 must wait 120 s of Booster 1, then 60 s of sustain.
    base=plug(0,"b1",True,3550)+plug(0,"b2",False,0)
    steps=ticks(0, 20)
    r=sim([{"funcs":F,"steps":steps}])[0]
    on_at=[s["at"] for s,x in zip(steps,r) if s["fn"]=="s" and x and x[0] and x[0]["payload"]["set"]]
    check("Booster 2 waits for Booster 1 to run 120 s plus a 60 s sustain, then turns ON once", on_at==[180000], on_at)
    comb=[x for s,x in zip(steps,r) if s["fn"]=="c"]
    check("combiner: dispatcher sees Booster 1's 3550 W as the hot-water plug", comb[-1][0]==[{"topic":"bsf/hotwater/plug_state","payload":True},{"topic":"bsf/hotwater/plug_power","payload":3550}])
    r=sim([{"funcs":F,"steps":plug(0,"b1",True,3550)+plug(0,"b2",True,3500)}])[0]
    check("combiner: both on => plug_power is the sum", r[-1][0][1]["payload"]==7050 and r[-1][0][0]["payload"] is True)
    r=sim([{"funcs":F,"steps":plug(0,"b1",True,3550)+plug(0,"b2",True,3500)+plug(200000,"b1",True,3550)}])[0]
    check("combiner: a booster not heard from for 150 s drops out of the sum", r[-1][0][1]["payload"]==3550)
    # running both, then each way Booster 2 must stop
    run=ticks(0, 19)+plug(185000,"b2",True,3500)
    def after(extra): return sim([{"funcs":F,"steps":run+extra}])[0][len(run):]
    check("Booster 1 OFF => Booster 2 OFF at once", cmds(after([status(190000, hwState="off")]))==[False])
    check("battery discharging > 2 kW => Booster 2 OFF at once", cmds(after([status(190000, batt_power=-2500)]))==[False])
    check("total load over the limit => Booster 2 OFF at once", cmds(after([status(190000, ac_load=10400)]))==[False])
    check("outside the daytime window => Booster 2 OFF at once", cmds(after([status(190000, mode="LOCKOUT")]))==[False])
    check("SOC below soc_off => Booster 2 OFF at once", cmds(after([status(190000, soc=89)]))==[False])
    check("dispatcher silent > 60 s => Booster 2 OFF on the tick", cmds(after([{"at":260000,"fn":"s","msg":{"topic":"tick"}}]))==[False])
    weak=ticks(190000, 9, b2=True, surplus_now=6400)   # 6400-3550-3500 = -650 spare
    rw=after(weak); offs=[st["at"] for st,x in zip(weak,rw) if x and isinstance(x[0],dict)]
    check("panels can't cover both (spare < 0, not curtailed) => OFF only after the 60 s sustain", cmds(rw)==[False] and offs==[250000], offs)
    check("...while curtailed it holds ON (the panels still have headroom)", cmds(after(ticks(190000, 12, b2=True, surplus_now=6400, curtailed=True)))==[])
    check("a 10 s dip does not touch Booster 2", cmds(after(ticks(190000, 1, b2=True, surplus_now=6400)+ticks(200000, 12, b2=True)))==[])
    # after an OFF it stays off for min_off (10 min) even in full sun, then comes back
    seq=[status(190000, batt_power=-2500)]+ticks(200000, 80)
    re=after(seq); ons=[st["at"] for st,x in zip(seq,re) if x and isinstance(x[0],dict) and x[0]["payload"]["set"]]
    check("after an OFF Booster 2 stays off 10 min, then comes back in full sun", ons[:1]==[190000+600000+60000], ons[:1])
    lo=sim([{"funcs":F,"steps":[status(0, mode="LOCKOUT", hwState="off")]+[status(t*10000, mode="LOCKOUT", hwState="off") for t in range(1,61)]}])[0]
    check("outside the window it re-sends OFF every 5 min", cmds(lo)==[False,False,False] , cmds(lo))
    check("never ON when SOC < soc_on (95%)", cmds(sim([{"funcs":F,"steps":ticks(0, 40, soc=94)}])[0])==[])
    check("never ON when total load + Booster 2 > limit", cmds(sim([{"funcs":F,"steps":ticks(0, 40, ac_load=7000)}])[0])==[])
    check("never ON with too little spare and no curtailment", cmds(sim([{"funcs":F,"steps":ticks(0, 40, surplus_now=7000)}])[0])==[])
    check("...but ON with the same spare when curtailed (battery full, panels throttled)", cmds(sim([{"funcs":F,"steps":ticks(0, 40, surplus_now=7000, curtailed=True)}])[0])==[True])

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
