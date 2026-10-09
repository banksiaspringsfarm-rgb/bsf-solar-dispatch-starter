#!/usr/bin/env python3
"""End-to-end bench for the Shelly hot-water driver, on the real pieces:
   simulated SP PRO point file -> publisher/selectlive_bridge.py -> MQTT -> the deploy.py-built flow in a real
   Node-RED -> Shelly RPC -> a simulated Shelly (tests/e2e/shelly_bench.js) -> back to the dispatcher + the relay.

Phases: sun => relay ON (with the dead-man) and stays ON without flapping (its own watts are subtracted) ·
the relay/state.json shows it ON with the relay's watts · cloud => relay OFF · sun => ON, then Node-RED is
killed => the relay switches ITSELF off when the dead-man runs out.

Needs: node, python3 + paho-mqtt, and a folder with node-red, node-red-contrib-tuya-smart-device, aedes, mqtt
(made on first run:  NR_DIR=<dir>, default ~/.cache/bsf-shelly-e2e). Port 1883 and 18800 must be free.
The dispatcher only switches inside its daytime window (05:30-15:30 AEST): run it then.
Run: python3 tests/e2e/run_shelly_e2e.py      (~5 min)"""
import json, os, shutil, signal, subprocess, sys, tempfile, threading, time, urllib.request
import paho.mqtt.client as mqtt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NR_DIR = os.environ.get("NR_DIR") or os.path.expanduser("~/.cache/bsf-shelly-e2e")
PREFIX, ELEMENT_W, DEADMAN_S, NR_PORT = "glen-eden/booster1", 3550, 120, 18800
fails = 0
def check(name, cond, detail=""):
    global fails
    print(("  ok " if cond else "  FAIL ") + name + ("" if cond else "  " + str(detail)[:300]), flush=True); fails += 0 if cond else 1

h = (time.gmtime().tm_hour + 10) % 24 + time.gmtime().tm_min / 60
if not (6.0 <= h < 15.0):
    sys.exit("It is %.1f h AEST: the dispatcher only switches between 05:30 and 15:30 AEST. Run this in daylight." % h)

if not os.path.exists(os.path.join(NR_DIR, "node_modules", "node-red")):
    os.makedirs(NR_DIR, exist_ok=True)
    subprocess.run("npm init -y >/dev/null && npm i --no-audit --no-fund node-red node-red-contrib-tuya-smart-device aedes mqtt",
                   shell=True, cwd=NR_DIR, check=True)

# ---- a throwaway copy of the bundle with a Glen-Eden-shaped config ----
work = tempfile.mkdtemp(prefix="bsf-shelly-e2e-")
repo = os.path.join(work, "repo")
shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", ".build", "config.json", "node_modules"))
cfg = json.load(open(os.path.join(ROOT, "config.example.json")))
cfg["site_name"] = "Bench"
cfg["hardware"].update({"nodered_url": "http://127.0.0.1:%d" % NR_PORT, "fronius": {"present": True, "label": "AC solar"}})
cfg["hardware"]["inverter"].update({"kind": "selectronic", "rating_kw": 7.5})
cfg["hardware"]["selectronic"].update({"ip": "127.0.0.1", "device_id": "bench", "poll_s": 2, "mqtt_host": "127.0.0.1"})
cfg["battery"].update({"chemistry": "lifepo4", "soc_danger_pct": 20, "soc_warn_pct": 40})
cfg["loads"] = [{"role": "hot_water", "label": "Booster 1", "driver": "shelly", "rated_w": 3600, "essential": False, "metered": True,
                 "shelly": {"topic_prefix": PREFIX, "switch_id": 0, "deadman_s": DEADMAN_S}}]
cfg["dispatcher"].update({"surplus_on_w": 4200, "surplus_off_w": 2500, "surplus_off_w_curt": 1500, "load_cap_w": 6500,
                          "safety_cap_w": 7000, "hw_element_w": 3600, "sustain_on_ms": 5000, "sustain_off_ms": 5000,
                          "sustain_off_ms_curt": 5000})
cfg["out_dir"] = os.path.join(work, "data"); os.makedirs(cfg["out_dir"])
json.dump(cfg, open(os.path.join(repo, "config.json"), "w"), indent=2)
scenario, point = os.path.join(work, "scenario.json"), os.path.join(work, "point.json")
def weather(pv_w):
    json.dump({"pv_w": pv_w, "house_w": 600, "soc": 100, "batt_w": -100}, open(scenario, "w"))
weather(9000)

procs = []
def start(cmd, name, **kw):
    f = open(os.path.join(work, name + ".log"), "w")
    p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, **kw); procs.append(p); return p
def cleanup():
    for p in procs:
        if p.poll() is None: p.terminate()
    for p in procs:
        try: p.wait(5)
        except Exception: p.kill()

events, msgs = [], []
try:
    bench = subprocess.Popen(["node", os.path.join(ROOT, "tests/e2e/shelly_bench.js"), "--prefix", PREFIX, "--element-w", str(ELEMENT_W),
                              "--point", point, "--scenario", scenario], stdout=subprocess.PIPE, text=True, cwd=NR_DIR,
                             env=dict(os.environ, NODE_PATH=os.path.join(NR_DIR, "node_modules")))
    procs.append(bench)
    threading.Thread(target=lambda: [events.append(json.loads(l)) for l in bench.stdout], daemon=True).start()
    time.sleep(2)
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="e2e-watch")
    sub.on_message = lambda c, u, m: msgs.append((time.time(), m.topic, m.payload.decode(errors="replace")))
    sub.connect("127.0.0.1", 1883); sub.subscribe("bsf/#"); sub.loop_start()
    start([sys.executable, os.path.join(repo, "publisher/selectlive_bridge.py"), "--config", os.path.join(repo, "config.json"), "--sample", point], "bridge")
    open(os.path.join(work, "settings.js"), "w").write("module.exports={flowFile:'%s',uiPort:%d,logging:{console:{level:'warn'}}};\n" % (os.path.join(work, "flows.json"), NR_PORT))
    nr = start(["node", os.path.join(NR_DIR, "node_modules/node-red/red.js"), "--userDir", NR_DIR, "--settings", os.path.join(work, "settings.js")],
               "nodered", env=dict(os.environ, TZ="Australia/Brisbane"))
    for _ in range(60):
        try: urllib.request.urlopen("http://127.0.0.1:%d/flows" % NR_PORT, timeout=2); break
        except Exception: time.sleep(1)
    r = subprocess.run([sys.executable, os.path.join(repo, "install/deploy.py"), "flow", "--deploy"], capture_output=True, text=True, cwd=repo)
    check("deploy.py built and deployed the Shelly flow", r.returncode == 0 and "Shelly: hot water switches" in r.stdout and "Deployed." in r.stdout, r.stdout[-600:] + r.stderr[-300:])
    t_deploy = time.time()

    def wait(pred, timeout):
        end = time.time() + timeout
        while time.time() < end:
            v = pred()
            if v: return v
            time.sleep(0.5)
        return None
    rpcs = lambda m: [e for e in events if e.get("ev") == "rpc" and e["method"] == m]
    outputs = lambda: [e for e in events if e.get("ev") == "output"]

    sc = wait(lambda: rpcs("Switch.SetConfig"), 30)
    check("on start the relay's power-on state is set to OFF", sc and sc[0]["params"] == {"id": 0, "config": {"initial_state": "off"}}, sc)
    check("the relay is polled for status", wait(lambda: rpcs("Switch.GetStatus"), 30) is not None)

    # ---- sun: ON ----
    on = wait(lambda: [e for e in outputs() if e["on"]], 90)
    check("full battery + 9 kW sun => the dispatcher switches the relay ON", bool(on), [e for e in events if e.get("ev") != "rpc"][-5:])
    sets = [e for e in rpcs("Switch.Set") if e["params"].get("on")]
    check("ON carries the dead-man (toggle_after %d s)" % DEADMAN_S, sets and sets[0]["params"].get("toggle_after") == DEADMAN_S, sets[:1])
    t_on = time.time()
    st = wait(lambda: [m for m in msgs if m[1] == "bsf/hotwater/plug_state" and m[2] == "true" and m[0] > t_deploy], 40)
    check("plug_state true reaches the dispatcher's topic", bool(st))
    pw = wait(lambda: [m for m in msgs if m[1] == "bsf/hotwater/plug_power" and m[2] == str(ELEMENT_W) and m[0] > t_deploy], 40)
    check("plug_power carries the relay's own %d W" % ELEMENT_W, bool(pw))
    # publisher (the relay that feeds the dashboard/widget) on the same broker
    pub = start([sys.executable, os.path.join(repo, "publisher/solar_state_publisher.py")], "publisher", env=dict(os.environ, BSF_CONFIG=os.path.join(repo, "config.json")))
    time.sleep(45)
    check("held ON for 45 s with the element drawing — no flapping", [e["on"] for e in outputs()] == [True], outputs())
    hs = [json.loads(m[2]) for m in msgs if m[1] == "bsf/hotwater/status"]
    last = hs[-1] if hs else {}
    check("dispatcher sees hwState on, plug_power %d, surplus ~8.4 kW (element subtracted)" % ELEMENT_W,
          last.get("hwState") == "on" and last.get("plug_power") == ELEMENT_W and 8000 <= last.get("surplus_now", 0) <= 8800, last)
    refresh = [e for e in rpcs("Switch.Set") if e["params"].get("on") and e["t"] / 1000 > t_on + 5]
    check("the dead-man is refreshed while ON (every 30 s)", len(refresh) >= 1, len(refresh))
    sj = os.path.join(cfg["out_dir"], "state.json")
    s = json.load(open(sj)) if os.path.exists(sj) else {}
    L = s.get("loads") or {}
    check("state.json: hot water connected, ON, %d W live, house load net of it" % ELEMENT_W,
          L.get("hot_water_connected") is True and L.get("hot_water_state") == "on" and L.get("hot_water_w") == ELEMENT_W
          and L.get("hot_water_metered") is True and L.get("essential_other_w") is not None and 400 <= L["essential_other_w"] <= 800, L)

    # ---- cloud: OFF ----
    weather(2000)
    off = wait(lambda: [e for e in outputs() if not e["on"]], 60)
    check("cloud (2 kW) => the dispatcher switches the relay OFF", bool(off) and off[0]["why"] == "Switch.Set", outputs())
    time.sleep(12)
    sj_s = json.load(open(sj)).get("loads") or {}
    check("state.json follows it to OFF, 0 W", sj_s.get("hot_water_state") == "off" and sj_s.get("hot_water_w") == 0, sj_s)

    # ---- sun again, then the controller dies ----
    weather(9000)
    n_on = len([e for e in outputs() if e["on"]])
    check("sun again => ON again", wait(lambda: len([e for e in outputs() if e["on"]]) > n_on, 90) is not None)
    t_kill = time.time(); nr.send_signal(signal.SIGKILL)
    print("  .. Node-RED killed with the relay ON; waiting up to %d s for the relay's own dead-man" % (DEADMAN_S + 40), flush=True)
    fb = wait(lambda: [e for e in events if e.get("ev") == "flip_back"], DEADMAN_S + 40)
    took = (fb[0]["t"] / 1000 - t_kill) if fb else None
    check("controller dead => the relay switches ITSELF off within the dead-man (%s s)" % (round(took) if took else "-"),
          bool(fb) and outputs()[-1]["on"] is False and took <= DEADMAN_S + 1, outputs()[-2:])
finally:
    cleanup()
    print("\n  logs: %s" % work)
print("\n%d failure(s)" % fails); sys.exit(1 if fails else 0)
