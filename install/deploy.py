#!/usr/bin/env python3
"""
BSF Solar Dispatch Starter — installer / orchestrator.

Stdlib only. Reads ONE config.json (see config.example.json) and:
  - stamps every site-specific value into the Node-RED flow (token substitution,
    node --check verified),
  - generates the dashboard's runtime config,
  - installs the Python publisher as a background service,
  - smoke-tests live data.

Subcommands:  check · flow · dashboard · publisher · widget · smoke · all
Flags:  --target DIR · --deploy · --dry-run · --no-start · --force
"""
import re, argparse, json, os, ssl, sys, subprocess, shutil, urllib.request, time, platform, tempfile

HERE     = os.path.dirname(os.path.abspath(__file__))
ROOT     = os.path.dirname(HERE)
CFG_PATH = os.path.join(ROOT, "config.json")
SRC_FLOW = os.path.join(ROOT, "node-red", "bsf-solar-dispatch.flow.json")
SHELLY_HW_TAB = os.path.join(ROOT, "node-red", "shelly-hot-water.flow.json")
HW2_TAB  = os.path.join(ROOT, "node-red", "hot-water-2.flow.json")
SRC_PUB  = os.path.join(ROOT, "publisher", "solar_state_publisher.py")
DASH_DIR = os.path.join(ROOT, "dashboard")
APK      = os.path.join(ROOT, "widget", "android", "BSF-Solar-Dispatch-v3.apk")
LABEL    = "farm.bsf.solar-dispatch-starter"

C_OK,C_WARN,C_ERR,C_DIM,C_END = "\033[92m","\033[93m","\033[91m","\033[90m","\033[0m"
def ok(m):   print(f"{C_OK}  ok {m}{C_END}")
def warn(m): print(f"{C_WARN}  ! {m}{C_END}")
def err(m):  print(f"{C_ERR}  x {m}{C_END}")
def step(m): print(f"\n{C_DIM}-- {m} --{C_END}")

# tested-default for every dispatcher token (so a missing/blank config value can NEVER
# produce broken JS — it falls back to the original shipped behaviour).
DISP_DEFAULTS = {
  "__DISP_SURPLUS_ON_W__":1800,"__DISP_SURPLUS_OFF_W__":1200,"__DISP_SURPLUS_OFF_W_CURT__":300,
  "__DISP_ELEMENT_W__":1600,"__DISP_LOAD_CAP_W__":4000,"__DISP_SAFETY_CAP_W__":4200,
  "__DISP_SUSTAIN_OFF_MS_CURT__":180000,"__DISP_SUSTAIN_OFF_MS__":60000,"__DISP_SUSTAIN_ON_MS__":60000,
  "__DISP_CURTAIL_FRONIUS_W__":50,
  "__DISP_W1_SOC_ON__":80,"__DISP_W1_SOC_OFF__":75,"__DISP_W2_SOC_ON__":90,"__DISP_W2_SOC_OFF__":85,
  "__DISP_W3_SOC_ON__":99,"__DISP_W3_SOC_OFF__":98,
  "__DISP_END_OFFSET_MIN__":120,
  "__AC_W1_SOC_ON__":80,"__AC_W1_SOC_OFF__":75,"__AC_W2_SOC_ON__":90,"__AC_W2_SOC_OFF__":85,
  "__AC_W3_SOC_ON__":99,"__AC_W3_SOC_OFF__":98,
  "__AC_SUSTAIN_ON_MS__":60000,"__AC_SUSTAIN_OFF_MS__":60000,
  "__AC_INSIDE_ON_DEFAULT__":19,"__AC_INSIDE_OFF_DEFAULT__":22,
}

def load_cfg():
    if not os.path.exists(CFG_PATH):
        err(f"config.json not found at {CFG_PATH}")
        print("    Copy the template first:  cp config.example.json config.json")
        print("    (or open this folder with Claude Code and say 'install BSF Solar Dispatch')")
        sys.exit(2)
    with open(CFG_PATH) as f:
        return json.load(f)

def _g(d, path, default=None):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur: return default
        cur = cur[k]
    return cur
def _num(v, d): return v if isinstance(v,(int,float)) and not isinstance(v,bool) else d
def _strip_scheme(h): return (h or "").replace("https://","").replace("http://","").rstrip("/")
def _loads_by_role(cfg):
    return {l.get("role"): l for l in (cfg.get("loads") or []) if isinstance(l, dict)}
def _driver(load): return ((load or {}).get("driver") or "tuya").strip().lower()
def _shelly_cfg(load):
    s=(load or {}).get("shelly") or {}
    return {"topic_prefix": (s.get("topic_prefix") or "").strip(), "switch_id": s.get("switch_id", 0),
            "deadman_s": s.get("deadman_s", 300)}
def _connected(load):
    """A load something can actually switch: a Tuya device id, or a Shelly topic prefix."""
    if not load: return False
    return bool(_shelly_cfg(load)["topic_prefix"]) if _driver(load)=="shelly" else bool(load.get("device_id"))
SHELLY_PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$")   # an MQTT topic prefix, no wildcards

def _ssl_unverified():
    c = ssl.create_default_context(); c.check_hostname=False; c.verify_mode=ssl.CERT_NONE; return c

# ---- chemistry -> SOC display bands -------------------------------------------
CHEM_BANDS = {"lead-acid":(60,75), "lifepo4":(20,40), "lithium-ion":(20,40)}
def soc_bands(cfg):
    chem = (_g(cfg,"battery.chemistry","lead-acid") or "lead-acid").lower()
    d_def,w_def = CHEM_BANDS.get(chem, (60,75))
    return _num(_g(cfg,"battery.soc_danger_pct"), d_def), _num(_g(cfg,"battery.soc_warn_pct"), w_def), chem

# ---- token map (config -> every flow token) -----------------------------------
def token_map(cfg):
    H=cfg.get("hardware",{}) or {}; T=cfg.get("tuya",{}) or {}; D=cfg.get("dispatcher",{}) or {}
    sw=D.get("soc_windows",{}) or {}; ac=D.get("ac",{}) or {}; loc=cfg.get("location",{}) or {}
    L=_loads_by_role(cfg)
    def lf(role, field): return ((L.get(role) or {}).get(field) or "")
    w=lambda win,side: _num((sw.get(win) or {}).get(side), DISP_DEFAULTS[f"__DISP_{win.upper()}_SOC_{side.upper()}__"])
    m = {
      "__CERBO_IP__": (_g(H,"selectronic.mqtt_host") or "127.0.0.1") if _inverter_kind(cfg)=="selectronic" else (H.get("cerbo_ip") or "192.168.1.50"),
      "__VRM_PORTAL_ID__": H.get("vrm_portal_id") or "",
      "__TUYA_ACCESS_ID__": T.get("access_id") or "",
      "__TUYA_ACCESS_SECRET__": T.get("access_secret") or "",
      "__TUYA_API_HOST__": _strip_scheme(T.get("api_host") or "openapi.tuyaus.com"),
      "__LAT__": repr(_num(loc.get("lat"), -27.47)),
      "__LON__": repr(_num(loc.get("lon"), 153.02)),
      "__TUYA_DEVICE_HW__": lf("hot_water","device_id"),
      "__TUYA_DEVICE_AC__": lf("air_con","device_id"),
      "__TUYA_DEVICE_INSIDE__": lf("inside_temp","device_id"),
      "__TUYA_LOCAL_KEY_HW__": lf("hot_water","local_key"),
      "__TUYA_LOCAL_KEY_AC__": lf("air_con","local_key"),
      "__TUYA_LOCAL_KEY_INSIDE__": lf("inside_temp","local_key"),
      "__TUYA_LOCAL_KEY_WELLPUMP__": lf("well_pump","local_key"),
      "__TUYA_LOCAL_IP_HW__": lf("hot_water","local_ip"),
      "__TUYA_LOCAL_IP_AC__": lf("air_con","local_ip"),
      "__TUYA_LOCAL_IP_INSIDE__": lf("inside_temp","local_ip"),
      "__TUYA_LOCAL_IP_WELLPUMP__": lf("well_pump","local_ip"),
    }
    disp = {
      "__DISP_SURPLUS_ON_W__":D.get("surplus_on_w"),"__DISP_SURPLUS_OFF_W__":D.get("surplus_off_w"),
      "__DISP_SURPLUS_OFF_W_CURT__":D.get("surplus_off_w_curt"),"__DISP_CURTAIL_FRONIUS_W__":D.get("curtail_fronius_w"),
      "__DISP_LOAD_CAP_W__":D.get("load_cap_w"),"__DISP_SAFETY_CAP_W__":D.get("safety_cap_w"),
      "__DISP_ELEMENT_W__":D.get("hw_element_w"),
      "__DISP_SUSTAIN_ON_MS__":D.get("sustain_on_ms"),"__DISP_SUSTAIN_OFF_MS__":D.get("sustain_off_ms"),
      "__DISP_SUSTAIN_OFF_MS_CURT__":D.get("sustain_off_ms_curt"),
      "__DISP_W1_SOC_ON__":w("w1","on"),"__DISP_W1_SOC_OFF__":w("w1","off"),
      "__DISP_W2_SOC_ON__":w("w2","on"),"__DISP_W2_SOC_OFF__":w("w2","off"),
      "__DISP_W3_SOC_ON__":w("w3","on"),"__DISP_W3_SOC_OFF__":w("w3","off"),
      "__DISP_END_OFFSET_MIN__":D.get("dispatch_end_offset_min"),
      "__AC_W1_SOC_ON__":w("w1","on"),"__AC_W1_SOC_OFF__":w("w1","off"),
      "__AC_W2_SOC_ON__":w("w2","on"),"__AC_W2_SOC_OFF__":w("w2","off"),
      "__AC_W3_SOC_ON__":w("w3","on"),"__AC_W3_SOC_OFF__":w("w3","off"),
      "__AC_SUSTAIN_ON_MS__":D.get("sustain_on_ms"),"__AC_SUSTAIN_OFF_MS__":D.get("sustain_off_ms"),
      "__AC_INSIDE_ON_DEFAULT__":_g(D,"ac.inside_temp_on_c"),"__AC_INSIDE_OFF_DEFAULT__":_g(D,"ac.inside_temp_off_c"),
    }
    for k,v in disp.items():
        m[k] = str(int(v) if isinstance(v,(int,float)) and not isinstance(v,bool) else DISP_DEFAULTS[k])
    return m

# ---- validation ---------------------------------------------------------------
def validate(cfg):
    issues=[]; warns=[]
    chem=(_g(cfg,"battery.chemistry","lead-acid") or "").lower()
    if chem not in CHEM_BANDS: warns.append(f"battery.chemistry '{chem}' unknown — using lead-acid bands.")
    dpct,wpct,_=soc_bands(cfg)
    if dpct>=wpct: issues.append(f"battery.soc_danger_pct ({dpct}) must be < soc_warn_pct ({wpct}).")
    if chem in ("lifepo4","lithium-ion") and dpct>40:
        warns.append(f"lithium chemistry with soc_danger_pct={dpct} is conservative (typical ~20).")
    if chem=="lead-acid" and dpct<55:
        warns.append(f"lead-acid with soc_danger_pct={dpct} is aggressive — lead-acid shouldn't sit low.")
    D=cfg.get("dispatcher",{}) or {}
    son,soff=_num(D.get("surplus_on_w"),1800),_num(D.get("surplus_off_w"),1200)
    if son<=soff: issues.append(f"dispatcher.surplus_on_w ({son}) must be > surplus_off_w ({soff}).")
    lc,sc=_num(D.get("load_cap_w"),4000),_num(D.get("safety_cap_w"),4200)
    if sc<lc: issues.append(f"dispatcher.safety_cap_w ({sc}) must be >= load_cap_w ({lc}).")
    sw=D.get("soc_windows",{}) or {}
    last=0
    for win in ("w1","w2","w3"):
        o,f=_num((sw.get(win) or {}).get("on"),0),_num((sw.get(win) or {}).get("off"),0)
        if o and f and o<=f: issues.append(f"soc_windows.{win}: on ({o}) must be > off ({f}).")
        if o and o<last: warns.append(f"soc_windows.{win}.on ({o}) < previous window — windows usually rise through the day.")
        last=o or last
    ac=D.get("ac",{}) or {}
    if (ac.get("mode") or "heating")=="cooling":
        warns.append("dispatcher.ac.mode='cooling' flips dashboard labels + setpoints only; the dispatcher ships HEATING-direction (see troubleshooting → Summer cooling).")
    elif _num(ac.get("inside_temp_on_c"),19)>=_num(ac.get("inside_temp_off_c"),22):
        issues.append("heating mode: dispatcher.ac.inside_temp_on_c must be < inside_temp_off_c.")
    if not (cfg.get("loads") or []): warns.append("no loads defined — nothing to dispatch (publisher/dashboard still run).")
    for l in (cfg.get("loads") or []):
        if not isinstance(l, dict): continue
        role=l.get("role"); drv=_driver(l)
        if drv not in ("tuya","shelly"): issues.append(f"loads[{role}].driver '{drv}' — must be 'tuya' or 'shelly'.")
        if drv!="shelly": continue
        if role not in ("hot_water","hot_water_2"): issues.append(f"loads[{role}].driver='shelly': only hot_water / hot_water_2 can use a Shelly today.")
        sc=_shelly_cfg(l)
        if not SHELLY_PREFIX_RE.match(sc["topic_prefix"]):
            issues.append(f"loads[{role}].shelly.topic_prefix '{sc['topic_prefix']}' — set it to the Shelly's MQTT topic prefix (letters, digits, - _ /; no + or #).")
        if not (isinstance(sc["switch_id"],int) and not isinstance(sc["switch_id"],bool) and 0<=sc["switch_id"]<=3):
            issues.append(f"loads[{role}].shelly.switch_id must be 0-3 (0 on a single relay).")
        if not (isinstance(sc["deadman_s"],(int,float)) and not isinstance(sc["deadman_s"],bool) and 120<=sc["deadman_s"]<=3600):
            issues.append(f"loads[{role}].shelly.deadman_s must be 120-3600 s (it is refreshed every 30 s while ON).")
        if not _num(l.get("rated_w"),0)>0:
            issues.append(f"loads[{role}].rated_w must be the element's watts — it stands in for the meter while the relay is ON.")
    L=_loads_by_role(cfg); h2=L.get("hot_water_2")
    if h2 and _driver(h2)=="shelly":
        if _driver(L.get("hot_water"))!="shelly":
            issues.append("loads[hot_water_2] is a Shelly: Booster 1 (loads[hot_water]) must be a Shelly too.")
        elif _shelly_cfg(h2)["topic_prefix"]==_shelly_cfg(L["hot_water"])["topic_prefix"]:
            issues.append("hot_water and hot_water_2 have the same shelly.topic_prefix — each relay needs its own.")
        c2=_hw2_cfg(cfg)
        if c2["soc_on"]<=c2["soc_off"]: issues.append(f"dispatcher.hot_water_2.soc_on ({c2['soc_on']}) must be > soc_off ({c2['soc_off']}).")
        if c2["surplus_on_w"]<=c2["surplus_off_w"]: issues.append("dispatcher.hot_water_2.surplus_on_w must be > surplus_off_w.")
        if _num(D.get("safety_cap_w"),4200)<=c2["max_total_w"]:
            issues.append(f"dispatcher.safety_cap_w ({_num(D.get('safety_cap_w'),4200)}) must be > dispatcher.hot_water_2.max_total_w ({c2['max_total_w']}): Booster 2 has to shed before the safety cap trips Booster 1.")
        if c2["max_total_w"] < _num(L["hot_water"].get("rated_w"),0)+c2["rated_w"]:
            warns.append(f"dispatcher.hot_water_2.max_total_w ({c2['max_total_w']} W) is below both boosters together: Booster 2 will rarely if ever run.")
    kind=_inverter_kind(cfg)
    if kind not in ("victron","selectronic"): issues.append(f"hardware.inverter.kind '{kind}' — must be 'victron' or 'selectronic'.")
    if kind=="selectronic":
        if not (_g(cfg,"hardware.selectronic.ip") and _g(cfg,"hardware.selectronic.device_id")):
            warns.append("selectronic: hardware.selectronic.ip / device_id not set — the bridge has nothing to poll (find the id at http://<ip>/cgi-bin/solarmonweb/devices/).")
        if _g(cfg,"hardware.fronius.present") is False and _num(_g(cfg,"hardware.selectronic.curtail_min_pv_w"),0)>0:
            warns.append("selectronic: no AC-coupled inverter and no shunt → the bridge cannot see solar; curtailment will never assert.")
        warns.append("selectronic: curtailment is SYNTHESISED (SOC/battery-W/solar heuristic) — confirm the three curtail_* knobs against a real full-battery midday.")
    return issues, warns

# ---- check --------------------------------------------------------------------
def cmd_check(cfg, args):
    step("Validating config.json")
    issues,warns = validate(cfg)
    kind=_inverter_kind(cfg); base=_nodered_url(cfg)
    if kind=="selectronic":
        sip=_g(cfg,"hardware.selectronic.ip",""); ok(f"inverter = Selectronic SP PRO via Select.live {sip or '(ip unset)'} → MQTT {_g(cfg,'hardware.selectronic.mqtt_host') or '127.0.0.1'}")
    else:
        cerbo=_g(cfg,"hardware.cerbo_ip","")
        (warn if (not cerbo or cerbo.endswith(".50")) else ok)(f"cerbo_ip = {cerbo or '(unset)'}")
    dpct,wpct,chem=soc_bands(cfg); ok(f"battery = {chem} (SOC red <= {dpct}%, amber < {wpct}%)")
    nplugs=len(cfg.get("loads") or []); ok(f"{nplugs} load(s) configured")
    for l in (cfg.get("loads") or []):
        if isinstance(l,dict) and _driver(l)=="shelly":
            ok(f"{l.get('role')} = Shelly over MQTT, prefix '{_shelly_cfg(l)['topic_prefix']}' (point the relay's MQTT at this site's broker)")
    if _g(cfg,"tuya.access_id","").startswith("YOUR_"): warn("Tuya access_id not set (Tuya features idle).")
    for w_ in warns: warn(w_)
    for i in issues: err(i)
    step(f"Reaching the Node-RED admin API at {base}")
    try: ok(f"Node-RED {base} reachable (flow rev {_flow_rev(base)}).")
    except Exception as e: warn(f"Node-RED {base} not reachable ({e.__class__.__name__}); stage files now, deploy when online.")
    print(); (err if issues else ok)(f"check complete — {len(issues)} blocker(s), {len(warns)} warning(s).")
    return len(issues)

# ---- flow ---------------------------------------------------------------------
def _flow_rev(base):
    req=urllib.request.Request(f"{base}/flows", headers={"Node-RED-API-Version":"v2"})
    with urllib.request.urlopen(req, timeout=8, context=_ssl_unverified() if base.startswith("https") else None) as r:
        return json.loads(r.read().decode()).get("rev")


def _git_build():
    """Short commit of the bundle this site was deployed from; travels with every feedback note."""
    try: return subprocess.run(["git","-C",ROOT,"rev-parse","--short","HEAD"],capture_output=True,text=True,timeout=5).stdout.strip() or None
    except Exception: return None

def _inverter_kind(cfg):
    return (_g(cfg,"hardware.inverter.kind") or "victron").strip().lower()

def _nodered_url(cfg):
    u=(_g(cfg,"hardware.nodered_url") or "").strip().rstrip("/")
    if u: return u
    if _inverter_kind(cfg)=="selectronic": return "http://127.0.0.1:1880"
    return "https://%s:1881" % (_g(cfg,"hardware.cerbo_ip","") or "")

# Victron input node name -> Select.live bridge topic suffix (see publisher/selectlive_bridge.py).
_SEL_TOPIC_BY_NAME = {
    "Battery SOC":"soc", "soc":"soc", "pv_dc":"pv_dc", "pv_fronius":"pv_fronius",
    "ac_load":"ac_load", "batt_power":"batt_power",
    "mode:mode_288":"mode_288", "mode:mode_289":"mode_289", "mode:mode_rs1":"mode_rs1", "mode:mode_rs2":"mode_rs2",
}
def selectronic_transform(flows, prefix="sel"):
    """Replace every victron-input-* node with an `mqtt in` node on the same wires,
    fed by the Select.live bridge. Nodes with no bridge topic (per-MPPT probes) become
    mqtt-ins on a topic the bridge never publishes: the dispatcher treats them as
    'no data' (documented: both-null = don't trust), which is the safe branch.
    The victron-client config node is dropped (the palette isn't installed on a Pi)."""
    broker=next((n["id"] for n in flows if n.get("type")=="mqtt-broker"), None)
    if not broker: raise RuntimeError("flow has no mqtt-broker node to attach Select.live inputs to")
    out=[]; swapped=[]
    for n in flows:
        t=n.get("type","")
        if t=="victron-client": continue
        if t.startswith("victron-input"):
            nm=n.get("name") or n["id"]
            suffix=_SEL_TOPIC_BY_NAME.get(nm) or re.sub(r"[^a-z0-9_]+","_",nm.lower())
            out.append({"id":n["id"],"type":"mqtt in","z":n.get("z"),"name":nm,
                        "topic":"%s/%s"%(prefix,suffix),"qos":"0","datatype":"json","broker":broker,
                        "nl":False,"rap":True,"rh":0,"inputs":0,"x":n.get("x",100),"y":n.get("y",100),
                        "wires":n.get("wires",[])})
            swapped.append((nm,"%s/%s"%(prefix,suffix)))
        else: out.append(n)
    return out, swapped

def _shelly_tab(load, broker, ids="shelly.hw.", label="Shelly — Hot Water", out="bsf/hotwater/plug_"):
    """The adapter tab for one Shelly, from node-red/shelly-hot-water.flow.json. ids/label/out let a second
    booster get its own copy (node ids, tab name, and the topics it reports on)."""
    sc=_shelly_cfg(load)
    if not SHELLY_PREFIX_RE.match(sc["topic_prefix"]):
        raise RuntimeError("loads[%s].shelly.topic_prefix is not set (run `deploy.py check`)" % load.get("role"))
    tm={"__SHELLY_PREFIX__":sc["topic_prefix"], "__SHELLY_SWITCH_ID__":str(int(sc["switch_id"])),
        "__SHELLY_SRC__":"bsf-dispatch-"+sc["topic_prefix"].replace("/","-"),
        "__SHELLY_DEADMAN_S__":str(int(sc["deadman_s"])),
        "__SHELLY_METERED__":"true" if load.get("metered", True) else "false",
        "__SHELLY_RATED_W__":str(int(_num(load.get("rated_w"),0))), "__SHELLY_BROKER__":broker}
    raw=open(SHELLY_HW_TAB).read()
    for k,v in tm.items(): raw=raw.replace(k,v)
    raw=raw.replace("shelly.hw.", ids).replace("Shelly — Hot Water", label).replace("bsf/hotwater/plug_", out)
    return json.loads(raw)

# dispatcher.hot_water_2 defaults: Booster 2 only joins Booster 1, only on a nearly full battery, sheds first.
HW2_DEFAULTS = {"surplus_on_w": None, "surplus_off_w": 0, "soc_on": 95, "soc_off": 90, "max_total_w": None,
                "batt_trip_w": 2000, "sustain_on_ms": 60000, "sustain_off_ms": 60000,
                "min_primary_on_ms": 120000, "min_off_ms": 600000}
def _hw2_cfg(cfg):
    D=(cfg.get("dispatcher") or {}).get("hot_water_2") or {}
    L2=_loads_by_role(cfg).get("hot_water_2") or {}
    c={k:(D.get(k) if _num(D.get(k),None) is not None else v) for k,v in HW2_DEFAULTS.items()}
    c["rated_w"]=_num(L2.get("rated_w"),0)
    if c["surplus_on_w"] is None: c["surplus_on_w"]=c["rated_w"]+600            # Booster 2 + 600 W still spare
    if c["max_total_w"] is None:  c["max_total_w"]=int(_num(_g(cfg,"hardware.inverter.rating_kw"),5.0)*1000)
    return c

def hot_water_2_transform(flows, cfg):
    """Booster 2 = a secondary load that only runs alongside Booster 1. Both report on their own topics
    (bsf/hotwater/b1|b2/plug_*); a combiner feeds the dispatcher their SUM on the original plug topics, so
    its surplus is always 'solar minus the house' and Booster 2 can never make it shed Booster 1. The
    secondary dispatcher (node-red/hot-water-2.flow.json) reads the dispatcher's status and switches
    Booster 2 through its own adapter; it sheds before Booster 1 does."""
    L=_loads_by_role(cfg); hw1,hw2=L["hot_water"],L["hot_water_2"]
    flows,dropped=shelly_transform(flows, hw1, out="bsf/hotwater/b1/plug_")
    broker=next(n["id"] for n in flows if n.get("type")=="mqtt-broker")
    tab2=_shelly_tab(hw2, broker, ids="shelly.hw2.", label="Shelly — Hot Water 2", out="bsf/hotwater/b2/plug_")
    raw=open(HW2_TAB).read().replace("__HW2_BROKER__", broker).replace("__HW2_CFG__", json.dumps(json.dumps(_hw2_cfg(cfg)))[1:-1])   # embedded inside JSON strings
    sec=json.loads(raw)
    next(n for n in tab2 if n["id"]=="shelly.hw2.linkin")["links"]=["hw2.linkout"]
    next(n for n in sec if n["id"]=="hw2.linkout")["links"]=["shelly.hw2.linkin"]
    return flows+tab2+sec, dropped

def shelly_transform(flows, load, out="bsf/hotwater/plug_"):
    """Swap the Tuya hot-water node for the Shelly adapter tab (node-red/shelly-hot-water.flow.json).
    The dispatcher + lockout loop already send their plug commands through a link-out; the link-in that
    fed the Tuya node is removed and the adapter's own link-in takes its place on that link-out, so
    every command reaches the Shelly and nothing reaches a Tuya node with no device."""
    broker=next((n["id"] for n in flows if n.get("type")=="mqtt-broker"), None)
    if not broker: raise RuntimeError("flow has no mqtt-broker node for the Shelly adapter")
    tab=_shelly_tab(load, broker, out=out)
    tuya_ids={n["id"] for n in flows if n.get("type")=="tuya-smart-device" and n.get("id")=="hot-water-tuya"}
    feeders=[n for n in flows if n.get("type")=="link in" and any(w in tuya_ids for ws in n.get("wires",[]) for w in ws)]
    if not tuya_ids or not feeders: raise RuntimeError("could not find the Tuya hot-water node and the link-in feeding it")
    drop=tuya_ids|{n["id"] for n in feeders}
    sources={src for n in feeders for src in n.get("links",[])}
    linkin=next(n for n in tab if n["id"]=="shelly.hw.linkin"); linkin["links"]=sorted(sources)
    out=[]
    for n in flows:
        if n["id"] in drop: continue
        if n["id"] in sources:
            n=dict(n); n["links"]=[l for l in n.get("links",[]) if l not in drop]+["shelly.hw.linkin"]
        out.append(n)
    return out+tab, sorted(drop)

CERBO_ARCHIVE="/data/home/nodered/bsf-archive"
def archive_dir(cfg):
    """Where Node-RED appends the permanent event + daily logs (events-YYYY-MM-DD.jsonl, daily-…, state-latest.json).
    The flow ships with the Cerbo's /data path; a Pi has no /data and Node-RED can't create it, so every write there
    failed silently. Default on a Selectronic/Pi install: ~/bsf-solar-dispatch/archive of the user running deploy.py
    (Node-RED runs as that same user on the Pi)."""
    d=(_g(cfg,"hardware.archive_dir") or "").strip().rstrip("/")
    if d: return os.path.expanduser(d)
    return os.path.join(os.path.expanduser("~"),"bsf-solar-dispatch","archive") if _inverter_kind(cfg)=="selectronic" else CERBO_ARCHIVE

def build_flow(cfg):
    raw=open(SRC_FLOW).read()
    tm=token_map(cfg)
    for tok,val in tm.items(): raw=raw.replace(tok, str(val))
    raw=raw.replace(CERBO_ARCHIVE, archive_dir(cfg))
    leftover=sorted(set(re.findall(r"__[A-Z0-9_]+__", raw)))
    flows=json.loads(raw)                                  # validates JSON post-substitution
    hw=_loads_by_role(cfg).get("hot_water"); hw2=_loads_by_role(cfg).get("hot_water_2")
    if hw2 and _driver(hw2)=="shelly":
        flows,dropped=hot_water_2_transform(flows, cfg); c2=_hw2_cfg(cfg)
        ok("Shelly: Booster 1 on '%s', Booster 2 on '%s' (joins Booster 1 when %d W spare or curtailed, SOC >= %d%%, total load <= %d W) (removed %s)"
           % (_shelly_cfg(hw)["topic_prefix"], _shelly_cfg(hw2)["topic_prefix"], c2["surplus_on_w"], c2["soc_on"], c2["max_total_w"], ", ".join(dropped)))
    elif hw and _driver(hw)=="shelly":
        flows,dropped=shelly_transform(flows, hw)
        ok("Shelly: hot water switches over MQTT RPC on '%s/rpc' (removed %s)" % (_shelly_cfg(hw)["topic_prefix"], ", ".join(dropped)))
    if _inverter_kind(cfg)=="selectronic":
        flows,swapped=selectronic_transform(flows, _g(cfg,"hardware.selectronic.topic_prefix") or "sel")
        ok("Selectronic: swapped %d Victron input nodes for MQTT inputs (%s)" % (len(swapped), ", ".join(t for _,t in swapped[:6])+("…" if len(swapped)>6 else "")))
    out=os.path.join(ROOT,".build"); os.makedirs(out, exist_ok=True)
    dest=os.path.join(out,"bsf-solar-dispatch.deployed.flow.json")
    json.dump(flows, open(dest,"w"), indent=2)
    return dest, flows, leftover

def _node_check(flows):
    node=shutil.which("node")
    if not node: return None
    bad=[]
    for n in flows:
        if isinstance(n,dict) and n.get("type")=="function" and n.get("func"):
            # async wrapper: Node-RED function nodes may use top-level await
            wrapped="(async function(){\n"+n["func"]+"\n})"
            with tempfile.NamedTemporaryFile("w",suffix=".js",delete=False) as f:
                f.write(wrapped); p=f.name
            r=subprocess.run([node,"--check",p], capture_output=True, text=True); os.unlink(p)
            if r.returncode!=0:
                msg=next((l.strip() for l in r.stderr.splitlines() if "Error" in l), r.stderr.strip()[:120])
                bad.append((n.get("name") or n.get("id"), msg))
    return bad

def cmd_flow(cfg, args):
    step("Stamping config into the Node-RED flow")
    dest,flows,leftover = build_flow(cfg)
    ok(f"Wrote substituted flow → {dest} ({len(flows)} nodes)")
    if leftover: warn("Un-substituted tokens remain (check config): "+", ".join(leftover))
    bad=_node_check(flows)
    if bad is None: warn("node not found — skipped JS syntax check of function bodies.")
    elif bad:
        for nm,e in bad: err(f"function '{nm}' failed node --check: {e}")
        err("Refusing to deploy a flow with broken function code."); return 1
    else: ok("All function bodies pass node --check.")
    if args.dry_run or not args.deploy:
        warn("Dry-run: NOT deploying to the Cerbo. Re-run with --deploy to push it live."); return 0
    base=_nodered_url(cfg)
    step(f"Deploying to Node-RED at {base} (type=flows — only changed tabs restart)")
    try:
        rev=_flow_rev(base)
        body=json.dumps({"flows":flows,"rev":rev}).encode()
        req=urllib.request.Request(f"{base}/flows", data=body, method="POST",
            headers={"Content-Type":"application/json","Node-RED-API-Version":"v2","Node-RED-Deployment-Type":"flows"})
        with urllib.request.urlopen(req, timeout=15, context=_ssl_unverified() if base.startswith("https") else None) as r:
            ok(f"Deployed. rev {rev} → {json.loads(r.read().decode()).get('rev')}")
    except Exception as e:
        err(f"Deploy failed: {e}")
        print("    Node-RED offline, or admin auth on (open %s). Paste the error into Claude Code." % base)
        return 1
    return 0

# ---- dashboard ----------------------------------------------------------------
def cmd_dashboard(cfg, args):
    step("Writing dashboard-config.js")
    tip_days=int(_num(_g(cfg,"dashboard.tip_after_days", _g(cfg,"dashboard.trial_days")),30))
    tip_url=_g(cfg,"dashboard.tip_url", _g(cfg,"dashboard.gumroad_url",""))
    dpct,wpct,chem=soc_bands(cfg); D=cfg.get("dispatcher",{}) or {}
    sw=D.get("soc_windows",{}) or {}; ac=D.get("ac",{}) or {}
    js={
      "siteName": cfg.get("site_name","Solar"),
      "cerboIp": "" if _inverter_kind(cfg)=="selectronic" else _g(cfg,"hardware.cerbo_ip","192.168.1.50"),   # "" = the host serving the page
      "lat": _num(_g(cfg,"location.lat"),-27.47), "lon": _num(_g(cfg,"location.lon"),153.02),
      "tipAfterDays": tip_days, "tipUrl": tip_url,
      "chemistry": chem, "socDanger": dpct, "socWarn": wpct,
      "currencySymbol": _g(cfg,"display.currency_symbol","$"), "pricePerKwh": _num(_g(cfg,"display.price_per_kwh"),0),
      "acMode": (ac.get("mode") or "heating"),
      # CFG mirror so the dashboard's display gates match the deployed dispatcher:
      "SURPLUS_ON": _num(D.get("surplus_on_w"),1800), "SURPLUS_OFF": _num(D.get("surplus_off_w"),1200),
      "ELEMENT": _num(D.get("hw_element_w"),1600), "AC_CAP": _num(D.get("load_cap_w"),4000),
      "AC_SAFETY": _num(D.get("safety_cap_w"),4200),
      "SOC_ON": _num((sw.get("w1") or {}).get("on"),80), "SOC_OFF": _num((sw.get("w1") or {}).get("off"),75),
      "INSIDE_ON": _num(ac.get("inside_temp_on_c"),19), "INSIDE_OFF": _num(ac.get("inside_temp_off_c"),22),
      "BATT_CAP_KWH": _g(cfg,"battery.capacity_kwh"),
      "build": _git_build(),
      # What THIS site actually has, so the page stops describing the author's farm (see the site-shape block in the dashboard):
      "acSolarName": (_g(cfg,"hardware.fronius.label","") or ("AC solar" if _inverter_kind(cfg)=="selectronic" else "Fronius")),
      "hwConnected": _connected(_loads_by_role(cfg).get("hot_water")),
      "hasAirCon": "air_con" in _loads_by_role(cfg),
      "sourceName": "system" if _inverter_kind(cfg)=="selectronic" else "Cerbo",
      "showBattery": bool(_g(cfg,"dashboard.show_battery_card",False)), "showDiag": bool(_g(cfg,"dashboard.show_connectivity_card",False)),
      "acLabel": (_loads_by_role(cfg).get("air_con") or {}).get("label") or "",
      "acLoadNote": _g(cfg,"display.ac_load_note","") or "",
      "chargers": [] if _inverter_kind(cfg)=="selectronic" else [{"key":c.get("key"),"label":c.get("label") or c.get("key"),"kind":c.get("kind")} for c in (_g(cfg,"hardware.chargers") or []) if c.get("key")],
    }
    dest=os.path.join(DASH_DIR,"dashboard-config.js")
    with open(dest,"w") as f:
        f.write("// Generated by install/deploy.py — edit freely, then refresh the dashboard.\n")
        f.write("window.BSF_CONFIG = "+json.dumps(js,indent=2)+";\n")
    ok(f"Wrote {dest}  (coffee card after {tip_days}d, {chem}, ac={js['acMode']})")
    return 0

# ---- publisher ----------------------------------------------------------------
def _target_dir(args):
    return os.path.abspath(args.target) if args.target else os.path.join(os.path.expanduser("~"),"bsf-solar-dispatch")

def cmd_publisher(cfg, args):
    tgt=_target_dir(args); step(f"Installing publisher → {tgt}")
    os.makedirs(tgt, exist_ok=True)
    data_dir=_g(cfg,"out_dir") or os.path.join(tgt,"dispatch-host"); os.makedirs(data_dir, exist_ok=True)
    shutil.copy2(SRC_PUB, os.path.join(tgt,"solar_state_publisher.py"))
    dash_srv=os.path.join(tgt,"dashboard_server.py"); fb_dir=os.path.join(tgt,"feedback")
    site_lbl=(cfg.get("site_name") or "Solar").replace('"',"")          # goes inside a quoted unit argument
    shutil.copy2(os.path.join(os.path.dirname(SRC_PUB),"dashboard_server.py"), dash_srv)
    sel = _inverter_kind(cfg)=="selectronic"
    if sel: shutil.copy2(os.path.join(os.path.dirname(SRC_PUB),"selectlive_bridge.py"), os.path.join(tgt,"selectlive_bridge.py"))
    shutil.copy2(os.path.join(DASH_DIR,"solar_dispatch_dashboard.html"), os.path.join(data_dir,"solar_dispatch_dashboard.html"))
    dcfg=os.path.join(DASH_DIR,"dashboard-config.js")
    if os.path.exists(dcfg): shutil.copy2(dcfg, os.path.join(data_dir,"dashboard-config.js"))
    resolved=dict(cfg); resolved["out_dir"]=data_dir
    cfg_dest=os.path.join(tgt,"config.json"); json.dump(resolved, open(cfg_dest,"w"), indent=2)
    ok(f"Staged publisher + config + dashboard (data dir {data_dir})")
    # The services run under the SAME Python as this installer (on a Pi: ~/.venv-bsf, which has paho-mqtt).
    # Picking "python3" off PATH instead once rewrote a Pi's units to the system Python with no paho, and the relay
    # + Select.live bridge crash-looped until they were reinstalled from the venv.
    py=sys.executable; sysname=platform.system()
    if subprocess.run([py,"-c","import paho.mqtt.client"], capture_output=True).returncode!=0:
        err(f"{py} has no paho-mqtt, so the relay would crash on start. Run deploy.py with the Python that has it "
            f"(on a Pi: ~/.venv-bsf/bin/python3 install/deploy.py publisher), or: {py} -m pip install -r publisher/requirements.txt")
        return 1
    if sysname=="Darwin":
        plist=os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
        if os.path.exists(plist) and not args.force: warn(f"{plist} exists — use --force. Skipping service.")
        else:
            _write_plist(plist, py, os.path.join(tgt,"solar_state_publisher.py"), cfg_dest, tgt)
            ok(f"Wrote launchd plist → {plist}")
            if args.no_start: warn(f"--no-start: load later with: launchctl bootstrap gui/$(id -u) {plist}")
            else:
                subprocess.run(["launchctl","bootout",f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
                r=subprocess.run(["launchctl","bootstrap",f"gui/{os.getuid()}",plist], capture_output=True, text=True)
                (ok if r.returncode==0 else warn)(f"launchctl bootstrap rc={r.returncode} {r.stderr.strip()}")
    elif sysname=="Linux":
        unit=os.path.expanduser(f"~/.config/systemd/user/{LABEL}.service"); os.makedirs(os.path.dirname(unit), exist_ok=True)
        if os.path.exists(unit) and not args.force: warn(f"{unit} exists — use --force. Skipping service.")
        else:
            _write_systemd(unit, py, os.path.join(tgt,"solar_state_publisher.py"), cfg_dest, tgt)
            ok(f"Wrote systemd unit → {unit}")
            if sel:
                bunit=os.path.expanduser(f"~/.config/systemd/user/{LABEL}-selectlive.service")
                _write_systemd(bunit, py, os.path.join(tgt,"selectlive_bridge.py"), cfg_dest, tgt, "Select.live -> MQTT bridge")
                ok(f"Wrote systemd unit → {bunit}")
            dunit=os.path.expanduser("~/.config/systemd/user/bsf-dashboard-http.service")
            open(dunit,"w").write(f"""[Unit]
Description=BSF Solar Dispatch dashboard + feedback drop-box (:8780)
After=network-online.target

[Service]
WorkingDirectory={data_dir}
ExecStart={py} {dash_srv} --dir {data_dir} --feedback-dir {fb_dir} --port 8780 --site "{site_lbl}"
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
""")
            ok(f"Wrote systemd unit → {dunit} (dashboard on :8780)")
            # The away view: read-only, token-gated, localhost-only. Inert until a tunnel is pointed at 127.0.0.1:8781.
            tok_path=os.path.join(tgt,"public_token")
            if not os.path.exists(tok_path):
                import secrets as _secrets
                fd=os.open(tok_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
                with os.fdopen(fd,"w") as tf: tf.write(_secrets.token_urlsafe(32)+"\n")
                ok("Generated the away-view link token (kept in public_token, mode 600; never regenerated on re-run)")
            punit=os.path.expanduser("~/.config/systemd/user/bsf-dashboard-public.service")
            open(punit,"w").write(f"""[Unit]
Description=BSF Solar Dispatch away view (read-only, token-gated, localhost only - for a tunnel)
After=network-online.target

[Service]
WorkingDirectory={data_dir}
ExecStart={py} {dash_srv} --dir {data_dir} --public --token-file {tok_path} --port 8781 --bind 127.0.0.1
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
""")
            ok(f"Wrote systemd unit → {punit} (away view on 127.0.0.1:8781)")
            _write_phone_setup(cfg, data_dir, open(tok_path).read().strip())
            if args.no_start: warn(f"--no-start: enable later: systemctl --user enable --now {LABEL}")
            else:
                # user services must outlive the SSH session on a headless Pi
                user=os.environ.get("USER") or ""
                r=subprocess.run(["loginctl","show-user",user,"-p","Linger","--value"], capture_output=True, text=True)
                if r.stdout.strip()!="yes":
                    r2=subprocess.run(["sudo","-n","loginctl","enable-linger",user], capture_output=True, text=True)
                    (ok if r2.returncode==0 else warn)("loginctl enable-linger " + ("ok — services run without a login" if r2.returncode==0 else f"failed — run: sudo loginctl enable-linger {user}"))
                subprocess.run(["systemctl","--user","daemon-reload"])
                for svc in ([LABEL, LABEL+"-selectlive"] if sel else [LABEL])+["bsf-dashboard-http","bsf-dashboard-public"]:
                    r=subprocess.run(["systemctl","--user","enable","--now",svc], capture_output=True, text=True)
                    subprocess.run(["systemctl","--user","restart",svc], capture_output=True)   # enable --now leaves a running unit on its OLD config
                    (ok if r.returncode==0 else warn)(f"systemctl enable + restart {svc} rc={r.returncode} {r.stderr.strip()}")
    else:
        warn(f"OS '{sysname}': run manually:  BSF_CONFIG={cfg_dest} {py} {os.path.join(tgt,'solar_state_publisher.py')}")
    print(f"\n  Dashboard served from: {data_dir}\n  Dashboard server:     {py} {dash_srv} --dir {data_dir} --feedback-dir {fb_dir}")
    return 0

def _write_phone_setup(cfg, data_dir, token):
    """phone-setup.html: served by the HOME dashboard server only (it is not on the away view's whitelist), so it is
    reachable on the house Wi-Fi and nowhere else. Open it on a phone that is on that Wi-Fi: install the widget app,
    then one tap fills the app in - the home address is simply the address the page was opened at, and the long away
    link never has to be typed or sent through a chat."""
    import html as _html
    away_base=(_g(cfg,"dashboard.away_base_url","") or "").strip().rstrip("/")
    away=f"{away_base}/{token}/" if away_base else ""
    chem=(_g(cfg,"battery.chemistry","lead-acid") or "").lower()
    site=(cfg.get("site_name") or "Solar")
    site_js=json.dumps(site).replace("<", "\\u003c")     # computed OUTSIDE the f-string: no backslashes allowed in f-string expressions before Python 3.12; and '<' escaped so a name can never close the script block
    apk_src=os.path.join(ROOT,"widget","android","solar-dispatch-widget.apk"); have_apk=os.path.exists(apk_src)
    if have_apk: shutil.copy2(apk_src, os.path.join(data_dir,"solar-dispatch-widget.apk"))
    page=f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><meta name="referrer" content="no-referrer"><title>Set up this phone</title>
<style>body{{margin:0;background:#0b1020;color:#e8ecf7;font:17px/1.5 -apple-system,Roboto,Segoe UI,sans-serif;padding:24px 20px 40px}}
h1{{font-size:24px;margin:0 0 6px}} p{{color:#8b96b5;margin:6px 0 18px}} ol{{padding-left:22px}} li{{margin:0 0 22px}}
a.btn{{display:block;text-align:center;text-decoration:none;font-weight:700;border-radius:12px;padding:16px;margin-top:10px;min-height:24px}}
.go{{background:#f5b62e;color:#1a1205}} .dl{{background:#232c47;color:#e8ecf7}} small{{color:#8b96b5;display:block;margin-top:8px;font-size:14px}}</style></head><body>
<h1>\u2600 {_html.escape(site)}</h1><p>Set up this phone's home-screen widget. Do this while the phone is on the house Wi-Fi.</p>
<ol><li><b>Install the app</b>{'<a class="btn dl" href="solar-dispatch-widget.apk">Download Solar Dispatch</a><small>Open the download and allow the install when Android asks.</small>' if have_apk else '<small>The app file is not on this box yet. Get it from the project&#39;s Releases page.</small>'}</li>
<li><b>Fill it in with one tap</b><a class="btn go" id="go" href="#">Set up this phone</a><small>The app opens already filled in. Press Test, then Save.</small></li>
<li><b>Add the widget</b><small>Long-press the home screen \u2192 Widgets \u2192 Solar Dispatch.</small></li></ol>
<script>(function(){{var q='name='+encodeURIComponent({site_js})+'&home='+encodeURIComponent(location.origin)+{json.dumps('&away='+__import__('urllib.parse').parse.quote(away,safe='') if away else '')}+'&lithium={'1' if chem in ('lifepo4','lithium-ion') else '0'}';
document.getElementById('go').href='intent://setup?'+q+'#Intent;scheme=solardispatch;package=farm.bsf.solardispatch;end';}})();</script></body></html>"""
    dest=os.path.join(data_dir,"phone-setup.html"); open(dest,"w").write(page)
    ok(f"Wrote {dest} (home Wi-Fi only){'' if away else ' - no dashboard.away_base_url set, so the link carries the home address only'}{'' if have_apk else ' - no widget APK to offer yet'}")

def _write_plist(path, py, script, cfg, wd):
    import plistlib
    plistlib.dump({"Label":LABEL,"ProgramArguments":[py,script],"EnvironmentVariables":{"BSF_CONFIG":cfg},
        "WorkingDirectory":wd,"RunAtLoad":True,"KeepAlive":True,
        "StandardOutPath":"/tmp/bsf-solar-dispatch.log","StandardErrorPath":"/tmp/bsf-solar-dispatch.err"}, open(path,"wb"))

def _write_systemd(path, py, script, cfg, wd, desc="publisher (read-only relay)"):
    open(path,"w").write(f"""[Unit]
Description=BSF Solar Dispatch {desc}
After=network-online.target

[Service]
Environment=BSF_CONFIG={cfg}
WorkingDirectory={wd}
ExecStart={py} {script}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
""")

# ---- widget / smoke / reset ---------------------------------------------------
def cmd_widget(cfg, args):
    step("Sideloading the Android widget APK")
    if not os.path.exists(APK):
        warn("No Android widget app in this bundle yet (see widget/android/README.md). Use the dashboard's 'Add to Home screen' for now; iPhone: widget/ios/README.md.")
        return 0
    adb=shutil.which("adb") or os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
    if not (shutil.which("adb") or os.path.exists(adb)):
        warn("adb not found. Copy the APK to your phone manually, or for iPhone see widget/ios/README.md."); print(f"      {APK}"); return 1
    if not os.path.exists(adb): adb=shutil.which("adb")
    devs=subprocess.run([adb,"devices"], capture_output=True, text=True).stdout
    if not [l for l in devs.splitlines()[1:] if l.strip() and "device" in l]:
        warn("No adb device. Wake/unlock the phone (wireless-debug sleeps on lock) and re-run `widget`."); return 1
    r=subprocess.run([adb,"install","-r",APK], capture_output=True, text=True)
    (ok if r.returncode==0 else err)(f"adb install rc={r.returncode}: {r.stdout.strip() or r.stderr.strip()}"); return r.returncode

def cmd_smoke(cfg, args):
    step("Smoke test — is live data flowing?")
    data_dir=_g(cfg,"out_dir") or os.path.join(_target_dir(args),"dispatch-host")
    state=os.path.join(data_dir,"state.json")
    for _ in range(6):
        if os.path.exists(state): break
        time.sleep(5)
    if not os.path.exists(state):
        warn(f"No {state} yet. Publisher running? Check /tmp/bsf-solar-dispatch.err"); return 1
    age=time.time()-os.path.getmtime(state); d=json.load(open(state))
    ok(f"state.json present ({age:.0f}s old) — ok={d.get('ok')} source={d.get('source')} hw_age_s={d.get('hw_age_s')}")
    (ok if (age<60 and d.get('ok')) else warn)("Fresh state." if (age<60 and d.get('ok')) else "Stale/ok=false — check the Cerbo broker.")
    return 0

def main():
    ap=argparse.ArgumentParser(description="BSF Solar Dispatch Starter installer")
    ap.add_argument("command", nargs="?", default="check",
        choices=["check","flow","dashboard","publisher","widget","smoke","all"])
    ap.add_argument("--target"); ap.add_argument("--deploy", action="store_true")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--force", action="store_true")
    args=ap.parse_args()
    print(f"{C_DIM}BSF Solar Dispatch Starter · {args.command}{C_END}")
    cfg=load_cfg(); rc=0
    fn={"check":cmd_check,"flow":cmd_flow,"dashboard":cmd_dashboard,"publisher":cmd_publisher,
        "widget":cmd_widget,"smoke":cmd_smoke}.get(args.command)
    if fn: rc=fn(cfg, args)
    elif args.command=="all":
        cmd_check(cfg,args); cmd_flow(cfg,args); cmd_dashboard(cfg,args); cmd_publisher(cfg,args); cmd_smoke(cfg,args)
    print()
    sys.exit(rc if isinstance(rc,int) else 0)

if __name__=="__main__": main()
