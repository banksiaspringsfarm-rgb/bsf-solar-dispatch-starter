#!/usr/bin/env python3
"""End-to-end bench for the Shelly hot-water drivers, on the real pieces:
   simulated SP PRO point file -> publisher/selectlive_bridge.py -> MQTT -> the deploy.py-built flow in a real
   Node-RED -> Shelly RPC -> simulated Shelly relays (tests/e2e/shelly_bench.js) -> back to the dispatcher + relay.

one: sun => relay ON (with the dead-man), held without flapping (its own watts are subtracted) · state.json shows
     it ON with the relay's watts · cloud => OFF · sun => ON, then Node-RED is killed => the relay switches
     ITSELF off when the dead-man runs out.
two: sun => Booster 1, then Booster 2 joins · thinner sun => Booster 2 sheds, Booster 1 stays · cloud => Booster 1 off.

Needs: node, python3 + paho-mqtt, and a folder with node-red, node-red-contrib-tuya-smart-device, aedes, mqtt
(made on first run:  NR_DIR=<dir>, default ~/.cache/bsf-shelly-e2e). Ports 1883 and 18800 must be free.
Node-RED's clock is shifted to 10:30 AEST (tests/e2e/clock_offset.js), so it runs at any hour.
Run: python3 tests/e2e/run_shelly_e2e.py [one|two|all]      (~5 min + ~4 min)"""
import json, os, shutil, signal, subprocess, sys, tempfile, threading, time, urllib.request
import paho.mqtt.client as mqtt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NR_DIR = os.environ.get("NR_DIR") or os.path.expanduser("~/.cache/bsf-shelly-e2e")
P1, P2, ELEMENT_W, DEADMAN_S, NR_PORT = "glen-eden/booster1", "glen-eden/booster2", 3550, 120, 18800
fails = 0
def check(name, cond, detail=""):
    global fails
    print(("  ok " if cond else "  FAIL ") + name + ("" if cond else "  " + str(detail)[:300]), flush=True); fails += 0 if cond else 1

if not os.path.exists(os.path.join(NR_DIR, "node_modules", "node-red")):
    os.makedirs(NR_DIR, exist_ok=True)
    subprocess.run("npm init -y >/dev/null && npm i --no-audit --no-fund node-red node-red-contrib-tuya-smart-device aedes mqtt",
                   shell=True, cwd=NR_DIR, check=True)

def clock_offset_ms():
    """Shift so Node-RED's 'now' is 10:30 AEST (00:30 UTC) today: inside the dispatch window, window W2."""
    now = time.time(); g = time.gmtime(now)
    target = time.mktime((g.tm_year, g.tm_mon, g.tm_mday, 0, 30, 0, 0, 0, 0)) - time.timezone if False else \
        (now - (g.tm_hour * 3600 + g.tm_min * 60 + g.tm_sec)) + 30 * 60
    return int((target - now) * 1000)

def wait(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v: return v
        time.sleep(0.5)
    return None

class Bench:
    def __init__(self, loads, disp_extra, prefixes):
        self.work = tempfile.mkdtemp(prefix="bsf-shelly-e2e-"); w = self.work
        self.repo = os.path.join(w, "repo")
        shutil.copytree(ROOT, self.repo, ignore=shutil.ignore_patterns(".git", ".build", "config.json", "node_modules"))
        cfg = json.load(open(os.path.join(ROOT, "config.example.json")))
        cfg["site_name"] = "Bench"
        cfg["hardware"].update({"nodered_url": "http://127.0.0.1:%d" % NR_PORT, "fronius": {"present": True, "label": "AC solar"}})
        cfg["hardware"]["inverter"].update({"kind": "selectronic", "rating_kw": 7.5})
        cfg["hardware"]["selectronic"].update({"ip": "127.0.0.1", "device_id": "bench", "poll_s": 2, "mqtt_host": "127.0.0.1"})
        cfg["battery"].update({"chemistry": "lifepo4", "soc_danger_pct": 20, "soc_warn_pct": 40})
        cfg["loads"] = loads
        cfg["dispatcher"].update({"surplus_on_w": 4200, "surplus_off_w": 2500, "surplus_off_w_curt": 1500, "load_cap_w": 6500,
                                  "safety_cap_w": 7000, "hw_element_w": 3600, "sustain_on_ms": 5000, "sustain_off_ms": 5000,
                                  "sustain_off_ms_curt": 5000})
        cfg["dispatcher"].update(disp_extra)
        cfg["out_dir"] = os.path.join(w, "data"); os.makedirs(cfg["out_dir"])
        self.cfg = cfg
        json.dump(cfg, open(os.path.join(self.repo, "config.json"), "w"), indent=2)
        self.scenario, self.point = os.path.join(w, "scenario.json"), os.path.join(w, "point.json")
        self.weather(9000)
        self.procs, self.events, self.msgs = [], [], []
        bench = subprocess.Popen(["node", os.path.join(ROOT, "tests/e2e/shelly_bench.js"), "--prefix", ",".join(prefixes),
                                  "--element-w", str(ELEMENT_W), "--point", self.point, "--scenario", self.scenario],
                                 stdout=subprocess.PIPE, text=True, cwd=NR_DIR, env=dict(os.environ, NODE_PATH=os.path.join(NR_DIR, "node_modules")))
        self.procs.append(bench)
        threading.Thread(target=lambda: [self.events.append(json.loads(l)) for l in bench.stdout], daemon=True).start()
        time.sleep(2)
        self.sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="e2e-watch-%d" % os.getpid())
        self.sub.on_message = lambda c, u, m: self.msgs.append((time.time(), m.topic, m.payload.decode(errors="replace")))
        self.sub.connect("127.0.0.1", 1883); self.sub.subscribe("bsf/#"); self.sub.loop_start()
        self.start([sys.executable, os.path.join(self.repo, "publisher/selectlive_bridge.py"), "--config", os.path.join(self.repo, "config.json"), "--sample", self.point], "bridge")
        open(os.path.join(w, "settings.js"), "w").write("module.exports={flowFile:'%s',uiPort:%d,logging:{console:{level:'warn'}}};\n" % (os.path.join(w, "flows.json"), NR_PORT))
        self.nr = self.start(["node", "--require", os.path.join(ROOT, "tests/e2e/clock_offset.js"), os.path.join(NR_DIR, "node_modules/node-red/red.js"),
                              "--userDir", NR_DIR, "--settings", os.path.join(w, "settings.js")], "nodered",
                             env=dict(os.environ, TZ="Australia/Brisbane", BSF_CLOCK_OFFSET_MS=str(clock_offset_ms())))
        for _ in range(60):
            try: urllib.request.urlopen("http://127.0.0.1:%d/flows" % NR_PORT, timeout=2); break
            except Exception: time.sleep(1)
        r = subprocess.run([sys.executable, os.path.join(self.repo, "install/deploy.py"), "flow", "--deploy"], capture_output=True, text=True, cwd=self.repo)
        self.deploy_out = r
        self.t_deploy = time.time()
    def weather(self, pv_w, batt_w=-100):
        json.dump({"pv_w": pv_w, "house_w": 600, "soc": 100, "batt_w": batt_w}, open(self.scenario, "w"))
    def start(self, cmd, name, **kw):
        f = open(os.path.join(self.work, name + ".log"), "w")
        p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, **kw); self.procs.append(p); return p
    def rpcs(self, m, relay=P1): return [e for e in self.events if e.get("ev") == "rpc" and e["method"] == m and e.get("relay") == relay]
    def outputs(self, relay=P1): return [e for e in self.events if e.get("ev") == "output" and e.get("relay") == relay]
    def close(self):
        try: self.sub.loop_stop(); self.sub.disconnect()
        except Exception: pass
        for p in self.procs:
            if p.poll() is None: p.terminate()
        for p in self.procs:
            try: p.wait(5)
            except Exception: p.kill()
        print("  logs: %s" % self.work, flush=True)

SH = lambda prefix: {"topic_prefix": prefix, "switch_id": 0, "deadman_s": DEADMAN_S}

def scenario_one():
    print("\n== one booster", flush=True)
    b = Bench([{"role": "hot_water", "label": "Booster 1", "driver": "shelly", "rated_w": 3600, "essential": False, "metered": True, "shelly": SH(P1)}], {}, [P1])
    try:
        r = b.deploy_out
        check("deploy.py built and deployed the Shelly flow", r.returncode == 0 and "Shelly: hot water switches" in r.stdout and "Deployed." in r.stdout, r.stdout[-600:] + r.stderr[-300:])
        sc = wait(lambda: b.rpcs("Switch.SetConfig"), 30)
        check("on start the relay's power-on state is set to OFF", sc and sc[0]["params"] == {"id": 0, "config": {"initial_state": "off"}}, sc)
        check("the relay is polled for status", wait(lambda: b.rpcs("Switch.GetStatus"), 30) is not None)
        on = wait(lambda: [e for e in b.outputs() if e["on"]], 90)
        check("full battery + 9 kW sun => the dispatcher switches the relay ON", bool(on), [e for e in b.events if e.get("ev") != "rpc"][-5:])
        sets = [e for e in b.rpcs("Switch.Set") if e["params"].get("on")]
        check("ON carries the dead-man (toggle_after %d s)" % DEADMAN_S, sets and sets[0]["params"].get("toggle_after") == DEADMAN_S, sets[:1])
        t_on = time.time()
        check("plug_state true reaches the dispatcher's topic", bool(wait(lambda: [m for m in b.msgs if m[1] == "bsf/hotwater/plug_state" and m[2] == "true" and m[0] > b.t_deploy], 40)))
        check("plug_power carries the relay's own %d W" % ELEMENT_W, bool(wait(lambda: [m for m in b.msgs if m[1] == "bsf/hotwater/plug_power" and m[2] == str(ELEMENT_W) and m[0] > b.t_deploy], 40)))
        b.start([sys.executable, os.path.join(b.repo, "publisher/solar_state_publisher.py")], "publisher", env=dict(os.environ, BSF_CONFIG=os.path.join(b.repo, "config.json")))
        time.sleep(45)
        check("held ON for 45 s with the element drawing — no flapping", [e["on"] for e in b.outputs()] == [True], b.outputs())
        hs = [json.loads(m[2]) for m in b.msgs if m[1] == "bsf/hotwater/status"]; last = hs[-1] if hs else {}
        check("dispatcher sees hwState on, plug_power %d, surplus ~8.4 kW (element subtracted)" % ELEMENT_W,
              last.get("hwState") == "on" and last.get("plug_power") == ELEMENT_W and 8000 <= last.get("surplus_now", 0) <= 8800, last)
        check("the dead-man is refreshed while ON (every 30 s)", len([e for e in b.rpcs("Switch.Set") if e["params"].get("on") and e["t"] / 1000 > t_on + 5]) >= 1)
        sj = os.path.join(b.cfg["out_dir"], "state.json")
        L = (json.load(open(sj)) if os.path.exists(sj) else {}).get("loads") or {}
        check("state.json: hot water connected, ON, %d W live, house load net of it" % ELEMENT_W,
              L.get("hot_water_connected") is True and L.get("hot_water_state") == "on" and L.get("hot_water_w") == ELEMENT_W
              and L.get("hot_water_metered") is True and L.get("essential_other_w") is not None and 400 <= L["essential_other_w"] <= 800, L)
        b.weather(2000)
        off = wait(lambda: [e for e in b.outputs() if not e["on"]], 60)
        check("cloud (2 kW) => the dispatcher switches the relay OFF", bool(off) and off[0]["why"] == "Switch.Set", b.outputs())
        time.sleep(12)
        L = json.load(open(sj)).get("loads") or {}
        check("state.json follows it to OFF, 0 W", L.get("hot_water_state") == "off" and L.get("hot_water_w") == 0, L)
        b.weather(9000)
        n_on = len([e for e in b.outputs() if e["on"]])
        check("sun again => ON again", wait(lambda: len([e for e in b.outputs() if e["on"]]) > n_on, 90) is not None)
        t_kill = time.time(); b.nr.send_signal(signal.SIGKILL)
        print("  .. Node-RED killed with the relay ON; waiting up to %d s for the relay's own dead-man" % (DEADMAN_S + 40), flush=True)
        fb = wait(lambda: [e for e in b.events if e.get("ev") == "flip_back"], DEADMAN_S + 40)
        took = (fb[0]["t"] / 1000 - t_kill) if fb else None
        check("controller dead => the relay switches ITSELF off within the dead-man (%s s)" % (round(took) if took else "-"),
              bool(fb) and b.outputs()[-1]["on"] is False and took <= DEADMAN_S + 1, b.outputs()[-2:])
    finally:
        b.close()

def scenario_two():
    print("\n== two boosters", flush=True)
    loads = [{"role": "hot_water", "label": "Booster 1", "driver": "shelly", "rated_w": 3600, "essential": False, "metered": True, "shelly": SH(P1)},
             {"role": "hot_water_2", "label": "Booster 2", "driver": "shelly", "rated_w": 3600, "essential": False, "metered": True, "shelly": SH(P2)}]
    b = Bench(loads, {"safety_cap_w": 11000, "hot_water_2": {"max_total_w": 10000, "sustain_on_ms": 5000, "sustain_off_ms": 5000,
                                                            "min_primary_on_ms": 15000, "min_off_ms": 60000}}, [P1, P2])
    try:
        r = b.deploy_out
        check("deploy.py built and deployed the two-booster flow", r.returncode == 0 and "Booster 2 on 'glen-eden/booster2'" in r.stdout and "Deployed." in r.stdout, r.stdout[-600:] + r.stderr[-300:])
        check("both relays get power-on OFF", wait(lambda: b.rpcs("Switch.SetConfig", P1) and b.rpcs("Switch.SetConfig", P2), 30) is not None)
        b.weather(12000)
        on1 = wait(lambda: [e for e in b.outputs(P1) if e["on"]], 90)
        check("12 kW sun => Booster 1 ON", bool(on1))
        check("Booster 2 is not switched in the same breath as Booster 1", not [e for e in b.outputs(P2) if e["on"] and on1 and e["t"] - on1[0]["t"] < 15000])
        on2 = wait(lambda: [e for e in b.outputs(P2) if e["on"]], 90)
        check("...then Booster 2 joins once Booster 1 has run 15 s and there is spare", bool(on2) and on2[0]["t"] - on1[0]["t"] >= 15000, (on1, on2))
        sets2 = [e for e in b.rpcs("Switch.Set", P2) if e["params"].get("on")]
        check("Booster 2's ON carries its own dead-man", sets2 and sets2[0]["params"].get("toggle_after") == DEADMAN_S)
        time.sleep(30)
        check("both held ON for 30 s, no flapping", [e["on"] for e in b.outputs(P1)] == [True] and [e["on"] for e in b.outputs(P2)] == [True], (b.outputs(P1), b.outputs(P2)))
        hs = [json.loads(m[2]) for m in b.msgs if m[1] == "bsf/hotwater/status"]; last = hs[-1] if hs else {}
        check("dispatcher sees both boosters as the hot-water plug (%d W) and the real surplus (~11.4 kW)" % (2 * ELEMENT_W),
              last.get("plug_power") == 2 * ELEMENT_W and 11000 <= last.get("surplus_now", 0) <= 11800, last)
        # thinner sun: 7 kW covers the house + one booster, not two; the battery starts covering the gap
        b.weather(7000, batt_w=1200)
        off2 = wait(lambda: [e for e in b.outputs(P2) if not e["on"]], 60)
        check("7 kW sun, battery discharging => Booster 2 sheds", bool(off2))
        time.sleep(20)
        check("...and Booster 1 stays ON (it keeps priority)", [e["on"] for e in b.outputs(P1)] == [True], b.outputs(P1))
        logs = [json.loads(m[2]) for m in b.msgs if m[1] == "bsf/hotwater/log"]
        check("Booster 2's on/off are in the dispatcher log", any(l.get("event", "").startswith("on_b2") for l in logs) and any(l.get("event", "").startswith("off_b2") for l in logs), [l.get("event") for l in logs][-6:])
        b.weather(2000, batt_w=2500)
        off1 = wait(lambda: [e for e in b.outputs(P1) if not e["on"]], 60)
        check("cloud => Booster 1 OFF too", bool(off1))
        time.sleep(15)
        check("Booster 2 never came back on while Booster 1 was off", not [e for e in b.outputs(P2) if e["on"] and off1 and e["t"] > off1[0]["t"]])
    finally:
        b.close()

mode = (sys.argv[1] if len(sys.argv) > 1 else "all")
if mode in ("one", "all"): scenario_one()
if mode in ("two", "all"): scenario_two()
print("\n%d failure(s)" % fails); sys.exit(1 if fails else 0)
