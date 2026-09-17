#!/usr/bin/env python3
"""
Select.live -> MQTT bridge (Selectronic SP PRO input adapter for BSF Solar Dispatch).

On a Victron system the dispatcher reads the Cerbo GX over D-Bus (victron-input
nodes). On a Selectronic SP PRO system there is no Cerbo: the Select.live box on
the LAN serves an unauthenticated JSON "point" every few seconds at

    http://<select_live_ip>/cgi-bin/solarmonweb/devices/<device_id>/point

This script polls that endpoint and republishes the readings the dispatcher
needs as plain numbers on the local MQTT broker (one topic per signal), so the
same dispatcher flow runs unchanged with `mqtt in` nodes in place of the
Victron ones (deploy.py does that swap when hardware.inverter.kind ==
"selectronic").

Topics (numeric payload, published every poll, NOT retained -- see pub() for why):

    sel/soc          battery_soc            %        (dispatcher: soc)
    sel/pv_dc        shunt_w  (DC-coupled)  W        (dispatcher: pv_dc)
    sel/pv_fronius   solarinverter_w        W        (dispatcher: pv_fronius = AC-coupled PV)
    sel/ac_load      load_w                 W        (dispatcher: ac_load)
    sel/batt_power   battery_w              W  +charging / -discharging (dispatcher: batt_power)
    sel/grid_w       grid_w                 W        (informational)
    sel/mode_288     1 = curtailed, 2 = producing   (dispatcher: mode_288; see below)
    sel/raw          the whole point JSON + bridge timestamp
    sel/bridge       {"ok":bool,"age_s":..,"err":..}  bridge health, every poll

Curtailment. The Victron flow gets a real "voltage/current limited" flag from
each MPPT. An SP PRO has no such flag on the API, so this bridge SYNTHESISES it
conservatively: curtailed = SOC >= curtail_soc_pct AND |battery_w| <= curtail_batt_w
AND it is daytime by the AC-coupled/DC solar reading (pv >= curtail_min_pv_w).
i.e. "battery full, solar barely charging, sun is up" -- which on an SP PRO
means it is frequency-shifting the AC-coupled inverter down. All three knobs
live in config.hardware.selectronic. When in doubt it reports NOT curtailed,
which only makes the dispatcher more conservative (plain surplus rule applies).

Sign conventions on the Select.live point (observed on SP PRO firmware 2.x):
    battery_w   > 0 charging, < 0 discharging
    grid_w      > 0 import,   < 0 export
    shunt_w     DC-coupled solar (MPPT via shunt), 0 if none fitted
    solarinverter_w  AC-coupled inverter output (Fronius/ABB/...)
If a site's signs turn out inverted, set hardware.selectronic.invert_battery_sign.

Usage:
    BSF_CONFIG=/path/config.json python3 selectlive_bridge.py          # service
    python3 selectlive_bridge.py --once                                # one poll, print, exit
    python3 selectlive_bridge.py --sample tests/selectlive_point.json  # no unit: replay a file
"""
import argparse, json, os, sys, time, urllib.request, urllib.error

DEFAULTS = {
    "poll_s": 10,
    "timeout_s": 5,
    "curtail_soc_pct": 95,
    "curtail_batt_w": 300,
    "curtail_min_pv_w": 400,
    "invert_battery_sign": False,
    "mqtt_host": "127.0.0.1",
    "mqtt_port": 1883,
    "topic_prefix": "sel",
}

def load_cfg(path=None):
    p = path or os.environ.get("BSF_CONFIG") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        cfg = json.load(open(p))
    except FileNotFoundError:
        print("[bridge] config not found at %s" % p, flush=True); cfg = {}
    hw = cfg.get("hardware", {}) or {}
    sel = dict(DEFAULTS); sel.update({k: v for k, v in (hw.get("selectronic") or {}).items() if not k.startswith("_")})
    if "mqtt_port" in hw and "mqtt_port" not in (hw.get("selectronic") or {}): sel["mqtt_port"] = hw["mqtt_port"]
    return cfg, sel

def point_url(sel):
    ip = (sel.get("ip") or "").strip(); dev = (sel.get("device_id") or "").strip()
    if not ip or not dev: return None
    return "http://%s/cgi-bin/solarmonweb/devices/%s/point" % (ip, dev)

def fetch_point(url, timeout):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as r:
        return json.loads(r.read().decode())

def num(v):
    try:
        f = float(v)
        return f if f == f and f not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None

def translate(point, sel):
    """Select.live point JSON -> {topic_suffix: value}. Pure; unit-tested."""
    items = point.get("items", point) if isinstance(point, dict) else {}
    soc   = num(items.get("battery_soc"))
    batt  = num(items.get("battery_w"))
    if batt is not None and sel.get("invert_battery_sign"): batt = -batt
    pv_dc = num(items.get("shunt_w")) or 0.0
    pv_ac = num(items.get("solarinverter_w")) or 0.0
    load  = num(items.get("load_w"))
    grid  = num(items.get("grid_w"))
    # Round at the source: the SP PRO hands out 8-decimal floats; SOC to 0.1 %, watts to whole W.
    out = {}
    if soc  is not None: out["soc"] = round(soc, 1)
    if batt is not None: out["batt_power"] = round(batt)
    out["pv_dc"] = round(pv_dc)
    out["pv_fronius"] = round(pv_ac)
    if load is not None: out["ac_load"] = round(load)
    if grid is not None: out["grid_w"] = round(grid)
    curtailed = (soc is not None and batt is not None
                 and soc >= float(sel["curtail_soc_pct"])
                 and abs(batt) <= float(sel["curtail_batt_w"])
                 and (pv_dc + pv_ac) >= float(sel["curtail_min_pv_w"]))
    out["mode_288"] = 1 if curtailed else 2
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config"); ap.add_argument("--once", action="store_true")
    ap.add_argument("--sample", help="replay a saved point JSON instead of polling (no unit needed)")
    ap.add_argument("--no-mqtt", action="store_true", help="print only")
    a = ap.parse_args()
    cfg, sel = load_cfg(a.config)
    url = point_url(sel)
    if not url and not a.sample:
        print("[bridge] hardware.selectronic.ip / device_id not set in config -- nothing to poll. "
              "Find the id at http://<ip>/cgi-bin/solarmonweb/devices/", flush=True); sys.exit(2)
    client = None
    if not a.no_mqtt:
        import paho.mqtt.client as mqtt
        try:    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="selectlive-bridge")  # paho 2.x
        except AttributeError: client = mqtt.Client(client_id="selectlive-bridge")                    # paho 1.6
        client.connect(sel["mqtt_host"], int(sel["mqtt_port"]), 60); client.loop_start()
    pre = sel["topic_prefix"].rstrip("/")
    def pub(suffix, payload, retain=False):
        # Measurements are NEVER retained: the dispatcher stamps a reading with its ARRIVAL time, so a retained
        # value re-delivered after a Node-RED restart would pass as fresh for the whole staleness window, however
        # old it really is. Only the bridge-health topic is retained (it carries its own timestamp).
        s = json.dumps(payload) if isinstance(payload, (dict, list)) else repr(payload) if isinstance(payload, float) else str(payload)
        if client: client.publish("%s/%s" % (pre, suffix), s, retain=retain)
    print("[bridge] polling %s every %ss -> mqtt://%s:%s/%s/*" % (url or a.sample, sel["poll_s"], sel["mqtt_host"], sel["mqtt_port"], pre), flush=True)
    fails = 0
    while True:
        t0 = time.time()
        try:
            point = json.load(open(a.sample)) if a.sample else fetch_point(url, float(sel["timeout_s"]))
            vals = translate(point, sel)
            for k, v in vals.items(): pub(k, v)
            point_ts = num((point.get("items") or {}).get("timestamp")) if isinstance(point, dict) else None
            age = (time.time() - point_ts) if point_ts else None
            pub("raw", {"point": point, "bridge_ts": int(time.time())})
            pub("bridge", {"ok": True, "age_s": None if age is None else round(age, 1), "ts": int(time.time())}, retain=True)
            fails = 0
            print("[bridge] " + " ".join("%s=%s" % (k, v) for k, v in vals.items()) + (" age=%.0fs" % age if age is not None else ""), flush=True)
        except (urllib.error.URLError, OSError, ValueError) as e:
            fails += 1
            pub("bridge", {"ok": False, "err": str(e)[:120], "fails": fails, "ts": int(time.time())}, retain=True)
            print("[bridge] poll failed (%d): %s" % (fails, e), flush=True)
        if a.once: break
        time.sleep(max(1.0, float(sel["poll_s"]) - (time.time() - t0)))
    if client: client.loop_stop(); client.disconnect()

if __name__ == "__main__":
    main()
