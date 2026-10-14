#!/usr/bin/env python3
"""Hot-water guard (dispatcher.hot_water_guard): minimum run / rest times and the frost-night hold, for a heat-pump
hot water. Runs the deploy.py-BUILT dispatcher, lockout loop and frost-forecast function in node
with a fake clock (Australia/Brisbane), so what is tested is what gets deployed.
Run: python3 tests/test_hw_guard.py"""
import json, os, shutil, subprocess, sys, importlib.util
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("deploy", os.path.join(ROOT, "install/deploy.py"))
sys.argv = ["deploy.py", "check"]; deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)
fails = 0
def check(name, cond, detail=""):
    global fails
    print(("  ok " if cond else "  FAIL ") + name + ("" if cond or detail == "" else "  -> " + str(detail)[:400])); fails += 0 if cond else 1

HEATPUMP = {"min_on_ms": 3600000, "min_off_ms": 900000, "frost": {"enabled": True, "min_c": 2, "soc_floor_pct": 60}}
def cfg(guard=None):
    c = json.load(open(os.path.join(ROOT, "config.example.json")))
    c["location"] = {"lat": -28.7, "lon": 151.9, "timezone": "Australia/Brisbane"}
    c["loads"] = [{"role": "hot_water", "label": "Sanden", "driver": "shelly", "rated_w": 1100, "metered": True,
                   "shelly": {"topic_prefix": "site-hotwater", "switch_id": 0, "deadman_s": 300}}]
    c["dispatcher"].update({"surplus_on_w": 1400, "surplus_off_w": 600, "hw_element_w": 1100, "sustain_on_ms": 60000,
                            "sustain_off_ms": 60000, "soc_windows": {"w1": {"on": 80, "off": 75}, "w2": {"on": 90, "off": 85}, "w3": {"on": 99, "off": 98}}})
    if guard is not None: c["dispatcher"]["hot_water_guard"] = guard
    return c
def fns(c):
    _, flows, left = deploy.build_flow(c)
    return {n["id"]: n["func"] for n in flows if n["type"] == "function"}, flows, left

# ---- build ----
f_off, flows_off, left = fns(cfg())
check("no tokens left in the default build", not [t for t in left if "GUARD" in t or "FROST" in t], left)
check("guard off by default: no Frost forecast tab", not any(n.get("z") == "frost.tab" for n in flows_off))
f_on, flows_on, _ = fns(cfg(HEATPUMP))
fr = [n for n in flows_on if n.get("z") == "frost.tab"]
check("frost on: the Frost forecast tab is added (inject, Open-Meteo request, function, mqtt out)",
      sorted(n["type"] for n in fr) == ["function", "http request", "inject", "mqtt out"])
http = next(n for n in fr if n["type"] == "http request")
check("the forecast is for the site's own lat/lon", "latitude=-28.7&longitude=151.9" in http["url"])
check("frost tab uses the flow's own broker", next(n for n in fr if n["type"] == "mqtt out")["broker"] == next(n["id"] for n in flows_on if n["type"] == "mqtt-broker"))
check("every function in the built flow passes node --check", not deploy._node_check(flows_on))
iss = lambda g: deploy.validate(cfg(g))[0]
check("valid guard: no blockers", not iss(HEATPUMP))
check("frost floor below the battery's danger floor is a blocker", any("soc_floor_pct" in i for i in iss(dict(HEATPUMP, frost=dict(HEATPUMP["frost"], soc_floor_pct=40)))))
check("a negative min_off is a blocker", any("min_off_ms" in i for i in iss(dict(HEATPUMP, min_off_ms=-1))))

# ---- run the functions ----
node = shutil.which("node")
if not node:
    print("  ! node not installed: skipped running the functions"); sys.exit(1)
HARNESS = r'''
const fs=require('fs'); const job=JSON.parse(fs.readFileSync(0,'utf8'));
const realNow=Date.now; let T=0; Date.now=()=>T;
const RealDate=Date;
global.Date=class extends RealDate { constructor(...a){ if(a.length===0) super(T); else super(...a); } static now(){ return T; } };
const ctx={}, flowS={}, glob={}; const res=[];
const mk=(store)=>({get:k=>store[k], set:(k,v)=>{store[k]=v;}});
const node={status(){}, warn(){}};
for (const st of job.steps) {
  T=st.t;
  if (st.global) Object.assign(glob, st.global);
  if (st.flow) Object.assign(flowS, st.flow);
  const fn=job.funcs[st.fn]; ctx[st.fn]=ctx[st.fn]||{};
  const f=new Function('msg','context','node','flow','global',fn);
  const out=f(st.msg, mk(ctx[st.fn]), node, mk(flowS), mk(glob));
  res.push({out:out, frost_hold:flowS.frost_hold||null, ff:glob.frost_forecast||null});
}
process.stdout.write(JSON.stringify(res));'''
def run(funcs, steps):
    r = subprocess.run([node, "-e", HARNESS], input=json.dumps({"funcs": funcs, "steps": steps}), capture_output=True,
                       text=True, timeout=60, env=dict(os.environ, TZ="Australia/Brisbane"))
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout)

DAY = 1784124000000           # 2026-07-16 00:00 AEST (14:00Z on the 15th), winter
def at(h, m=0): return DAY + int((h * 60 + m) * 60000)
SR, SS = "2026-07-16T05:30:00+10:00", "2026-07-16T17:30:00+10:00"   # window 05:30 - 15:30 (end offset 120 min)
def state(t, surplus=2500, soc=95, plug_on=False, stale=None):
    # house 400 W; pv makes the requested surplus over the house (the dispatcher subtracts the plug's own watts)
    plug_w = 1100 if plug_on else 0
    return {"fn": "hwv3.fn.dispatcher", "t": t, "flow": {"sunrise": SR, "sunset": SS},
            "msg": {"topic": "state", "payload": {"pv_dc": 0, "pv_fronius": surplus + 400, "ac_load": 400 + plug_w, "soc": soc,
                    "batt_power": 0, "plug_state": plug_on, "plug_power": plug_w, "stale": stale or {}}}}
def ev(r):
    o = r["out"]
    return None if not o or not o[1] else o[1]["payload"]["event"]
def cmd(r):
    o = r["out"]
    return None if not o or not o[0] else o[0]["payload"]["set"]
def boot(t, glob=None):
    """Node-RED started 16 min before t (a start counts as a stop, so the 15 min rest is already over)."""
    b = state(t - 16 * 60000, surplus=0, soc=50)
    if glob: b["global"] = glob
    return b
def drive(funcs, seq, booted=True):
    """seq: list of (t, kwargs). Tracks the plug the way the relay would (on after an ON command)."""
    steps, plug = ([boot(seq[0][0])] if booted else []), False
    out = []
    for t, kw in seq:
        r = run(funcs, steps + [state(t, plug_on=plug, **kw)])[-1]
        steps.append(state(t, plug_on=plug, **kw))
        if cmd(r) is not None: plug = cmd(r)
        out.append(r)
    return out

def minutes(a, b, step=1): return [a + i * 60000 for i in range(0, int((b - a) / 60000) + 1, step)]

# 1. guard off = the original: ON after the 60 s sustain, OFF 60 s after the surplus goes
seq = [(t, {}) for t in minutes(at(10), at(10, 2))] + [(t, {"surplus": 100}) for t in minutes(at(10, 5), at(10, 7))]
r = drive(f_off, seq)
evs = [e for e in (ev(x) for x in r) if e]
check("guard off: ON after the 60 s sustain, OFF 60 s after the surplus drops (original behaviour)", evs == ["on_surplus", "off_surplus"], evs)

# 2. min-on: surplus drops 5 min into the run -> held on until 60 min
seq = [(t, {}) for t in minutes(at(10), at(10, 1))] + [(t, {"surplus": 100}) for t in minutes(at(10, 5), at(11, 5), 5)]
r = drive(f_on, seq)
offs = [i for i, x in enumerate(r) if ev(x) == "off_surplus"]
check("min-on: a cloud 5 min into a run does not stop it before 60 min", offs and seq[offs[0]][0] >= at(11, 1), [(seq[i][0] - at(10, 1)) / 60000 for i in offs])
check("min-on: ...and it does stop once the hour is up", len(offs) == 1)

# 3. safety beats min-on: SOC falls through the window's floor 5 min in -> off at once
seq = [(t, {}) for t in minutes(at(10), at(10, 1))] + [(at(10, 6), {"soc": 80})]
r = drive(f_on, seq)
check("SOC floor still trips at once inside the min-on hour", ev(r[-1]) == "off_soc_floor", ev(r[-1]))

# 4. min-off: after a stop, sun comes back -> no restart for 15 min
seq = [(t, {}) for t in minutes(at(10), at(10, 1))] + [(t, {"surplus": 100}) for t in minutes(at(11, 1), at(11, 3))] \
      + [(t, {}) for t in minutes(at(11, 4), at(11, 22))]
r = drive(f_on, seq)
off_t = next(seq[i][0] for i, x in enumerate(r) if ev(x) == "off_surplus")
on2 = [seq[i][0] for i, x in enumerate(r) if ev(x) == "on_surplus"][1:]
check("min-off: no restart for 15 min after a stop, then it restarts", on2 and on2[0] - off_t >= 900000 and on2[0] - off_t <= 900000 + 120000,
      [(t - off_t) / 60000 for t in on2])

# 5. no new start inside min_on of the window's end (15:30)
seq = [(t, {"soc": 100}) for t in minutes(at(14, 40), at(14, 45))]   # after 13:00 the window needs 99 %
r = drive(f_on, seq)
check("no new start 50 min before the window ends (it couldn't run its hour)", not any(ev(x) == "on_surplus" for x in r))
r = drive(f_off, seq)
check("...but without the guard it starts as before", any(ev(x) == "on_surplus" for x in r))

# 5b. a Node-RED (re)start counts as a stop: the relay may have just been running
seq = [(t, {}) for t in minutes(at(10), at(10, 20))]
r = drive(f_on, seq, booted=False)
first = next((seq[i][0] for i, x in enumerate(r) if ev(x) == "on_surplus"), None)
check("after a restart the heat pump rests 15 min before its first start", first is not None and first - at(10) >= 900000, (first - at(10)) / 60000 if first else None)
r = drive(f_off, seq, booted=False)
check("...without the guard it starts after the usual 60 s", next(seq[i][0] for i, x in enumerate(r) if ev(x) == "on_surplus") - at(10) <= 120000)

# 6. frost night
FROSTY = {"frost_forecast": {"ts": at(15, 0), "night_min_c": -1.5, "frost": True}}
MILD = {"frost_forecast": {"ts": at(15, 0), "night_min_c": 6.0, "frost": False}}
def night(funcs, glob, seq, extra_steps=()):
    steps, plug, out = [boot(seq[0][0], glob)], False, []
    for t, kw in seq:
        s = state(t, plug_on=plug, **kw)
        r = run(funcs, steps + [s])[-1]
        steps.append(s)
        if cmd(r) is not None: plug = cmd(r)
        out.append(r)
    return out, steps
seq = [(t, {"surplus": 0, "soc": 70}) for t in minutes(at(16), at(16, 2))]
r, steps = night(f_on, FROSTY, seq)
check("frost night: powered at 16:00 despite SOC 70 % (below every window)", ev(r[0]) == "on_frost" and cmd(r[0]) is True, ev(r[0]))
check("frost night: status shows the hold and tonight's minimum", r[0]["out"][2]["payload"]["frost_hold"] is True and r[0]["out"][2]["payload"]["frost_night_min_c"] == -1.5)
lock = run(f_on, steps + [{"fn": "hwv3.fn.lockout", "t": at(16, 3), "msg": {}}])[-1]
check("lockout loop leaves it on while the dispatcher holds it for frost", lock["out"] is None)
r2, _ = night(f_on, FROSTY, seq + [(at(16, 4), {"surplus": 0, "soc": 59})])
check("frost hold never runs below its SOC floor (60 %)", ev(r2[-1]) == "off_frost_soc_floor" and cmd(r2[-1]) is False, ev(r2[-1]))
r3, st3 = night(f_on, MILD, seq)
check("mild night: no frost hold", not any(ev(x) == "on_frost" for x in r3))
lock = run(f_on, st3 + [{"fn": "hwv3.fn.lockout", "t": at(16, 5), "msg": {}}])[-1]
check("mild night: the lockout loop forces OFF as before", lock["out"] and lock["out"][0]["payload"]["set"] is False)
STALE = {"frost_forecast": {"ts": at(15, 0) - 13 * 3600000, "night_min_c": -3, "frost": True}}
r4, _ = night(f_on, STALE, seq)
check("a forecast older than 12 h: no frost hold (fails off, as before)", not any(ev(x) == "on_frost" for x in r4))
r5, _ = night(f_off, FROSTY, seq)
check("frost forecast present but the guard off: nothing changes", not any(ev(x) == "on_frost" for x in r5))
# latch: the forecast warms up at 01:00 -> it stays on; morning DISPATCH -> latch cleared
s = [boot(at(16), FROSTY), state(at(16), soc=70, surplus=0)]
r = run(f_on, s)
s.append(state(at(16, 1), soc=70, surplus=0, plug_on=True))
s.append(dict(state(at(1 + 24), soc=68, surplus=0, plug_on=True), **{"global": {"frost_forecast": {"ts": at(25), "night_min_c": 3.0, "frost": False}}}))
r = run(f_on, s)
check("latched for the night: a warmer update at 01:00 does not switch it off", ev(r[-1]) is None and r[-1]["out"][2]["payload"]["frost_hold"] is True)
# stale inputs on a frost night: fail-safe OFF still wins
s2 = s + [state(at(2 + 24), soc=68, surplus=0, plug_on=True, stale={"soc": True})]
r = run(f_on, s2)
check("stale inputs on a frost night: OFF (fail-safe kept)", ev(r[-1]) == "off_topic_stale")
# min-off before a frost start: stopped at ~15:22 -> the frost start at 15:31 waits until ~15:37
seq = [(t, {"soc": 100}) for t in minutes(at(14, 20), at(14, 21))] + [(t, {"surplus": 100, "soc": 100}) for t in minutes(at(15, 21), at(15, 24))] \
      + [(t, {"surplus": 0, "soc": 92}) for t in minutes(at(15, 31), at(15, 50))]
r, _ = night(f_on, FROSTY, seq)
off_t = next(seq[i][0] for i, x in enumerate(r) if ev(x) == "off_surplus")
fon = next((seq[i][0] for i, x in enumerate(r) if ev(x) == "on_frost"), None)
check("frost start respects the 15 min rest after a stop", fon is not None and fon - off_t >= 900000, (fon - off_t) / 60000 if fon else None)

# 7. the forecast function on an Open-Meteo-shaped reply
def om(start_local, temps):
    import datetime as dt
    t0 = dt.datetime.fromisoformat(start_local)
    return {"utc_offset_seconds": 36000, "hourly": {"time": [(t0 + dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(len(temps))],
                                                     "temperature_2m": temps}}
temps = [12] * 16 + [8, 6, 4, 3, 2.5, 2, 1.4, 1.1, 0.9, 0.7, 0.8, 1.5, 3, 5, 8, 11, 14] + [15] * 15   # from 00:00 on the 16th
fx = {"frost": f_on["frost.fn"]}
r = run(fx, [{"fn": "frost", "t": at(15, 30), "msg": {"payload": om("2026-07-16T00:00", temps)}}])
fc = r[0]["ff"]
check("forecast at 15:30: tonight's minimum to 09:00 tomorrow = 0.7 C -> frost", fc and fc["night_min_c"] == 0.7 and fc["frost"] is True and fc["until"] == "2026-07-17T09:00", fc)
r = run(fx, [{"fn": "frost", "t": at(15, 30), "msg": {"payload": om("2026-07-16T00:00", [max(v, 2.5) for v in temps])}}])
check("a 2.5 C night is not a frost night (threshold 2 C)", r[0]["ff"]["frost"] is False)
r = run(fx, [{"fn": "frost", "t": at(15, 30), "msg": {"payload": "Error: getaddrinfo ENOTFOUND", "statusCode": None}}])
check("a failed fetch keeps no new forecast (the old one goes stale)", r[0]["ff"] is None and r[0]["out"] is None)
r = run(fx, [{"fn": "frost", "t": at(24 + 3), "msg": {"payload": om("2026-07-16T00:00", temps)}}])
check("after midnight: the window runs to 09:00 the same morning", r[0]["ff"]["until"] == "2026-07-17T09:00" and r[0]["ff"]["night_min_c"] == 1.5, r[0]["ff"])   # 03:00-09:00 only: the 0.7 at 01:00 is past

print("\n%d failure(s)" % fails); sys.exit(1 if fails else 0)
