#!/usr/bin/env python3
"""
BSF Solar Dispatch — public relay publisher (READ-ONLY).

Subscribes to the Cerbo local MQTT broker and writes a SANITIZED snapshot of the
dispatcher's live state to disk every ~15 s. The bots app serves those files at
/solar/state and /solar/history, which cloudflared exposes publicly. The
whitelist below is the *only* thing that ever leaves the farm — no broker
address, no device IDs, no control surface, nothing but the energy numbers the
dashboard already shows.

Read-only by construction: the ONLY message this relay ever publishes is the
Victron `R/<portal>/keepalive` read-request (empty payload). That is a documented
Victron *read* trigger — it makes the broker republish its `N/` telemetry tree to
this client for ~60 s; it writes NO device state and NO setting (Victron setting
writes use the `W/` prefix, which this relay NEVER sends). It is required because
the per-charger `N/...` topics are silent unless a client is keepalive-ing them.

All numeric `ts` fields are epoch MILLISECONDS, to match the dashboard's
Date.now()/localStorage history. Run as launchd `farm.bsf.solar-publisher`
using /usr/bin/python3 (has paho).
"""
import json, os, time, tempfile, threading, ssl, urllib.request, calendar
import paho.mqtt.client as mqtt

# --- BSF Solar Dispatch Starter: site configuration --------------------------
# Every site-specific value (Cerbo IP, Tuya cloud keys, device IDs, VRM portal)
# lives in config.json next to this script (or the path in $BSF_CONFIG). The
# bundle ships config.example.json with PLACEHOLDERS only -- copy it to
# config.json and fill in your own values. Nothing site-specific is hard-coded.
def _load_cfg():
    import json as _j
    p = os.environ.get("BSF_CONFIG") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(p) as f:
            return _j.load(f)
    except FileNotFoundError:
        print("[config] config.json not found at %s -- running with placeholders "
              "(no live data until you create it)." % p)
        return {}
    except Exception as e:
        print("[config] WARNING: could not parse %s: %s" % (p, e))
        return {}

_CFG   = _load_cfg()
_HW    = _CFG.get("hardware", {}) if isinstance(_CFG.get("hardware"), dict) else {}
_TUYA  = _CFG.get("tuya", {}) if isinstance(_CFG.get("tuya"), dict) else {}
_DISP  = _CFG.get("dispatcher", {}) if isinstance(_CFG.get("dispatcher"), dict) else {}
# loads-by-role from the per-plug loads array (role -> {device_id,local_key,local_ip,...})
_LOADS = {l.get("role"): l for l in (_CFG.get("loads") or []) if isinstance(l, dict)}
def _load_dev(role):  # device_id for a given plug role ("" if absent)
    return (_LOADS.get(role) or {}).get("device_id", "") or ""

BROKER_HOST = _HW.get("cerbo_ip", "YOUR_CERBO_IP")
BROKER_PORT = int(_HW.get("mqtt_port", 1883) or 1883)
OUT_DIR     = _CFG.get("out_dir") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dispatch-host")
STATE_FILE  = os.path.join(OUT_DIR, "state.json")
HIST_FILE   = os.path.join(OUT_DIR, "history.json")

WRITE_EVERY_S   = 15                 # snapshot cadence to disk (seconds)
SAMPLE_EVERY_MS = 30 * 1000          # history sample cadence
HIST_MAX_AGE_MS = 24 * 3600 * 1000
HIST_MAX        = 3200
EVENTS_MAX      = 500

# ---- SANITISATION WHITELISTS -------------------------------------------------
# Only these keys are ever copied out of an incoming payload. Anything else the
# dispatcher might add in future is dropped by default (fail-closed).
HW_FIELDS = {
    "ts", "mode", "hwState", "soc", "pv_total", "pv_dc", "pv_fronius",
    "ac_load", "ac_other", "plug_power", "batt_power", "surplus_now",
    "surplus_after_on", "ac_after_on", "curtailed", "curtailed_heuristic",
    "mppt_curtailed", "fronius_curtailed", "mppt_other_pv",
    "mppt288_state", "mppt288_power", "mppt289_state", "mppt289_power",
    # canonical per-tracker throttle state (MppOperationMode: 0=off,1=throttled,2=mppt,255=n/a)
    "mode_288", "mode_289", "mode_rs1", "mode_rs2",
    "off_thresh_active", "off_sustain_active",
    "sunrise_iso", "sunset_iso", "stale",
}
AC_FIELDS = {
    "ts", "window", "acState", "soc", "hw_drawing", "hw_plug_power", "ac_load",
    "on_pct", "off_pct", "inside_temp", "inside_temp_usable", "inside_temp_age_s",
    "inside_temp_on_c", "inside_temp_off_c", "dispatch_end_iso", "preheat_start_iso",
    "thresholds_changed_ts", "thresholds_changed_src",
    # v1.11 presence-aware mode switch (dispatcher echoes these on bsf/ac/status)
    "ac_mode", "mode_changed_ts", "mode_changed_src",
    "anyone_home", "presence_known", "presence_configured",
}
EVENT_FIELDS = {"event", "reason", "soc", "surplus", "ac", "batt_power", "curtailed"}

TOPICS = [
    ("bsf/hotwater/status", 0), ("bsf/ac/status", 0),
    ("bsf/hotwater/log", 0), ("bsf/ac/log", 0),
    ("bsf/hotwater/anomaly", 0), ("bsf/ac/anomaly", 0),
    ("bsf/presence/state", 0),
]

# Presence relay (display-only). The detector's bsf/presence/state carries full MACs
# for the LAN registration UI; the PUBLIC snapshot must not leak household device MACs,
# so we copy only the non-identifying fields here. The LAN dashboard reads full presence
# (incl. MACs + candidates) straight off the broker over MQTT-WS — never via this relay.
def sanitize_presence(payload):
    if not isinstance(payload, dict):
        return None
    people = []
    for p in payload.get("people", []) or []:
        if not isinstance(p, dict):
            continue
        people.append({
            "slot": p.get("slot"), "name": p.get("name"),
            "registered": bool(p.get("registered")), "enabled": bool(p.get("enabled", True)),
            "home": bool(p.get("home")), "age_s": p.get("age_s"),
            "last_seen_ms": p.get("last_seen_ms"), "home_since_ms": p.get("home_since_ms"),
            "note": p.get("note"),
        })
    return {
        "ts": payload.get("ts"), "ts_ms": payload.get("ts_ms"),
        "anyone_home": bool(payload.get("anyone_home")),
        "configured": bool(payload.get("configured")),
        "grace_s": payload.get("grace_s"),
        "people": people,
    }

lock = threading.Lock()
state = {"hw": None, "ac": None, "hw_ts": 0, "ac_ts": 0,   # *_ts in ms
         "presence": None, "presence_ts": 0}
samples = []     # [{ts(ms), hw, ac}]
events = []      # [{ts(ms), src, kind, name, detail}]
_last_sample = 0

# ---- 7-day weekly rolling aggregates (display-only) --------------------------
# The 24 h history above is too short for the dashboard's "This week" stat cards.
# We keep a TINY per-LOCAL-DAY bucket of running accumulators (≤8 days) and
# integrate the live stream into them each writer tick. This is READ-ONLY relay
# work: it only summarizes numbers the dispatcher already publishes — it never
# touches dispatcher state or math. Footprint is fixed (a handful of floats per
# day), persisted to weekly.json and re-seeded on restart so a relay bounce does
# not reset the week. The resulting summary rides in state.json as snap["weekly"].
WEEK_FILE       = os.path.join(OUT_DIR, "weekly.json")
WEEK_DAYS       = 7                  # rolling window for the "This week" summary cards
ARCHIVE_DAYS    = 120                # per-day buckets retained for the detail-card Week/Month/Year axis
                                     # (~120 floats/day -> tiny; Year fills in over time)
SOC_FULL_PCT    = 100                # "at 100% SOC" => surplus being spilled
ACCUM_MAX_DT_MS = 120 * 1000         # cap per-tick integration; a longer gap = sleep/restart -> skip
HW_FRESH_MS     = 90 * 1000          # only integrate while the HW status feed is fresh
HW_NOMINAL_W    = int(_DISP.get("hw_element_w", 1614) or 1614)  # element draw when the plug meter is unavailable

weekly = {}          # "YYYY-MM-DD"(local) -> bucket of accumulators
_accum = {"ts": 0}   # last integration wall-clock (ms); reset to 0 on restart


def _wnum(x):
    """Finite number or None (rejects NaN/inf/bools-as-None-safe)."""
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and x not in (float("inf"), float("-inf")) else None


def _fresh_bucket():
    return {"span_s": 0.0, "soc_sum": 0.0, "soc_n": 0, "soc_min": None,
            "batt_min": None, "pv_wh": 0.0, "hw_on_s": 0.0, "hw_wh": 0.0, "full_s": 0.0,
            # --- per-node daily energy (Phase C detail-card Week/Month/Year) ---
            "pv_dc_wh": 0.0, "pv_fron_wh": 0.0, "sur_wh": 0.0, "ac_wh": 0.0,
            # --- per-load attribution energy (display-only; LIVE ticks only, forward) ---
            # ac_wh above = the FULL lumped Cerbo household load. These split it: air-con
            # (Daikin plug) + clean essentials (household minus HW minus air-con).
            "acond_wh": 0.0, "ess_other_wh": 0.0, "ac_on_s": 0.0,
            # --- curtailment (Phase A; forward-only from the canonical signal) ---
            # These are tracked only on LIVE ticks (never seeded from history): pre-2026-06-16
            # `curtailed` was the discredited heuristic, and the per-tracker modes don't exist in
            # the 24 h history at all. So curt_track_s / day_s are the curtailment-specific
            # coverage + daylight denominators, independent of span_s (which seeds from history).
            "curt_track_s": 0.0,   # fresh-feed seconds we've tracked curtailment over
            "day_s": 0.0,          # of those, seconds inside daylight (sunrise..sunset)
            "curt_s": 0.0,         # overall curtailed seconds (dispatcher's canonical hw.curtailed)
            "curt_288_s": 0.0, "curt_289_s": 0.0, "curt_rs1_s": 0.0, "curt_rs2_s": 0.0,  # per-tracker mode==1
            "forgone_wh": 0.0,     # estimated foregone energy (rough — demonstrated-ceiling differential)
            "pk_288": 0.0, "pk_289": 0.0, "pk_rs1": 0.0, "pk_rs2": 0.0}  # rolling uncurtailed peak per tracker (W)


# Tracker map: (bucket suffix, hw status mode field, hw.chargers watts key).
_TRACKERS = (("288", "mode_288", "m288"), ("289", "mode_289", "m289"),
             ("rs1", "mode_rs1", "rs1"), ("rs2", "mode_rs2", "rs2"))
_FORGONE_FLOOR_W = 50          # a tracker producing < this isn't a credible peak ceiling
_DEFAULT_KEYS = tuple(_fresh_bucket().keys())


def _ensure_keys(b):
    """Migrate a bucket loaded from an older weekly.json (or any partial bucket) so every
    curtailment key exists. Cheap + idempotent; lets new fields land without losing the
    seeded SOC/solar/HW history already in the file."""
    fresh = _fresh_bucket()
    for k in _DEFAULT_KEYS:
        if k not in b:
            b[k] = fresh[k]
    return b


def _iso_utc_ms(iso):
    """Parse a Victron UTC ISO ('...Z') to epoch ms. sunrise_iso/sunset_iso are UTC, so we
    must use calendar.timegm (NOT time.mktime, which assumes local time)."""
    if not iso:
        return None
    try:
        return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) * 1000
    except Exception:
        return None


def _day_key(t_ms):
    return time.strftime("%Y-%m-%d", time.localtime(t_ms / 1000.0))


def _bucket_for(t_ms):
    """Today's bucket, creating it if needed. A brand-new day carries forward the previous
    day's per-tracker peak ceilings so the foregone estimate has a sane reference in the
    morning instead of resetting to zero each midnight."""
    day = _day_key(t_ms)
    b = weekly.get(day)
    if b is None:
        b = _fresh_bucket()
        prior = [k for k in sorted(weekly.keys()) if k < day]
        if prior:
            pb = _ensure_keys(weekly[prior[-1]])
            for suf, _m, _c in _TRACKERS:
                b["pk_" + suf] = pb["pk_" + suf]
        weekly[day] = b
    return _ensure_keys(b)


def _accum_curtail(b, dt_s, dt_h, t_ms, hw, chargers):
    """Integrate one fresh LIVE interval of curtailment into bucket b. Overall curtailed time
    comes from the dispatcher's canonical hw.curtailed; per-tracker from the published modes
    (1 = MppOperationMode voltage/current-limited = genuinely throttling). Foregone energy is
    a rough estimate: each throttled tracker's loss = its best demonstrated uncurtailed output
    (pk) minus what it's making now, integrated over time."""
    b["curt_track_s"] += dt_s
    sr = _iso_utc_ms(hw.get("sunrise_iso")); ss = _iso_utc_ms(hw.get("sunset_iso"))
    if sr and ss and sr <= t_ms <= ss:
        b["day_s"] += dt_s
    if hw.get("curtailed"):
        b["curt_s"] += dt_s
    ch = chargers if isinstance(chargers, dict) else {}
    for suf, modef, ckey in _TRACKERS:
        mode = hw.get(modef)
        if mode == 1:
            b["curt_" + suf + "_s"] += dt_s
        w = _wnum(ch.get(ckey))
        if w is None:
            continue
        if mode == 2 and w > _FORGONE_FLOOR_W:        # uncurtailed + producing => update the ceiling
            if w > b["pk_" + suf]:
                b["pk_" + suf] = w
        elif mode == 1:                               # throttled => bank the differential vs the ceiling
            lost = b["pk_" + suf] - w
            if lost > 0:
                b["forgone_wh"] += lost * dt_h


def _prune_weekly(t_ms):
    cut = _day_key(t_ms - ARCHIVE_DAYS * 86400 * 1000)
    for k in [k for k in weekly if k < cut]:
        weekly.pop(k, None)


def _accum_into(b, dt_s, t_ms, hw, loads, chargers, live):
    """Integrate one interval (dt_s seconds) of a fresh hw sample into bucket b.
    live=True is a real-time tick: it reads the metered HW watts and accumulates the
    curtailment stats (overall + per-tracker + foregone). live=False is a 24 h-history
    seed sample — no plug meter (falls back to nominal element draw) and NO curtailment
    (pre-canonical `curtailed` was the heuristic; modes/per-charger watts aren't in history)."""
    _ensure_keys(b)
    use_plug = live
    dt_h = dt_s / 3600.0
    b["span_s"] += dt_s
    soc = _wnum(hw.get("soc"))
    if soc is not None:
        b["soc_sum"] += soc; b["soc_n"] += 1
        b["soc_min"] = soc if b["soc_min"] is None else min(b["soc_min"], soc)
        if soc >= SOC_FULL_PCT:
            b["full_s"] += dt_s
    bp = _wnum(hw.get("batt_power"))
    if bp is not None:
        b["batt_min"] = bp if b["batt_min"] is None else min(b["batt_min"], bp)
    pv = _wnum(hw.get("pv_total"))          # pv_total = DC + Fronius (total array output)
    if pv is not None and pv > 0:
        b["pv_wh"] += pv * dt_h
    # per-node daily energy integrals (display-only; same gating as pv_wh)
    pvdc = _wnum(hw.get("pv_dc"))
    if pvdc is not None and pvdc > 0:
        b["pv_dc_wh"] += pvdc * dt_h
    pvfr = _wnum(hw.get("pv_fronius"))
    if pvfr is not None and pvfr > 0:
        b["pv_fron_wh"] += pvfr * dt_h
    sur = _wnum(hw.get("surplus_now"))
    if sur is not None and sur > 0:
        b["sur_wh"] += sur * dt_h
    acl = _wnum(hw.get("ac_load"))
    if acl is not None and acl > 0:
        b["ac_wh"] += acl * dt_h
    if hw.get("hwState") == "on":
        b["hw_on_s"] += dt_s
        hw_w = None
        if use_plug and loads and loads.get("hot_water_metered") and loads.get("hot_water_w") is not None:
            hw_w = _wnum(loads.get("hot_water_w"))
        if hw_w is None or hw_w <= 0:
            hw_w = HW_NOMINAL_W
        b["hw_wh"] += hw_w * dt_h
    # Air-con + clean-essentials daily energy. LIVE ticks only (loads is None on the
    # 24 h-history seed), so these are forward-only — like the curtailment stats — and
    # use the same attributed ac_w / essential_other_w the dashboard shows (metered or
    # the held/estimated stand-in). Splits the lumped ac_wh into its real contributors.
    if use_plug and loads:
        if loads.get("ac_state") == "on" or loads.get("ac_estimated"):
            b["ac_on_s"] += dt_s
            acw = _wnum(loads.get("ac_w"))
            if acw is not None and acw > 0:
                b["acond_wh"] += acw * dt_h
        eo = _wnum(loads.get("essential_other_w"))
        if eo is not None and eo > 0:
            b["ess_other_wh"] += eo * dt_h
    if live:
        _accum_curtail(b, dt_s, dt_h, t_ms, hw, chargers)


def accum_weekly(t, hw, hw_fresh, loads, chargers):
    """Integrate the live stream into the per-day weekly buckets, gated on a fresh
    HW feed + a capped dt so a dead broker or a sleep gap can't inflate the totals."""
    last = _accum["ts"]
    _accum["ts"] = t                        # advance the clock every tick (fresh or not)
    if not hw_fresh or not isinstance(hw, dict):
        return
    if not last or (t - last) <= 0 or (t - last) > ACCUM_MAX_DT_MS:
        return                              # first tick or a gap — don't bridge it
    _accum_into(_bucket_for(t), (t - last) / 1000.0, t, hw, loads, chargers, True)
    _prune_weekly(t)


def weekly_summary(t):
    """Roll the retained day-buckets into the dashboard's 6 weekly stat cards.
    Returns None until there's at least one bucket. All values are display-only
    aggregates (no IDs, no control surface) — safe for the public snapshot."""
    wk_cut = _day_key(t - WEEK_DAYS * 86400 * 1000)   # summary = last 7 days only (archive keeps more)
    days = sorted(k for k in weekly.keys() if k >= wk_cut)
    if not days:
        return None
    soc_sum = soc_n = 0
    soc_min = batt_min = None
    span_s = pv_wh = hw_on_s = hw_wh = full_s = 0.0
    curt_track_s = day_s = curt_s = forgone_wh = 0.0
    acond_wh = ess_other_wh = ac_on_s = 0.0
    ct = {"288": 0.0, "289": 0.0, "rs1": 0.0, "rs2": 0.0}
    for k in days:
        b = _ensure_keys(weekly[k])
        soc_sum += b["soc_sum"]; soc_n += b["soc_n"]
        span_s += b["span_s"]; pv_wh += b["pv_wh"]
        hw_on_s += b["hw_on_s"]; hw_wh += b["hw_wh"]; full_s += b["full_s"]
        acond_wh += b["acond_wh"]; ess_other_wh += b["ess_other_wh"]; ac_on_s += b["ac_on_s"]
        curt_track_s += b["curt_track_s"]; day_s += b["day_s"]; curt_s += b["curt_s"]
        forgone_wh += b["forgone_wh"]
        for suf in ct:
            ct[suf] += b["curt_" + suf + "_s"]
        if b["soc_min"] is not None:
            soc_min = b["soc_min"] if soc_min is None else min(soc_min, b["soc_min"])
        if b["batt_min"] is not None:
            batt_min = b["batt_min"] if batt_min is None else min(batt_min, b["batt_min"])
    return {
        "avg_soc":         round(soc_sum / soc_n, 1) if soc_n else None,
        "min_soc":         round(soc_min) if soc_min is not None else None,
        "max_discharge_w": round(-batt_min) if (batt_min is not None and batt_min < 0) else 0,
        "solar_kwh":       round(pv_wh / 1000.0, 1),
        "hw_hours":        round(hw_on_s / 3600.0, 1),
        "hw_kwh":          round(hw_wh / 1000.0, 1),
        "ac_hours":        round(ac_on_s / 3600.0, 1),
        "acond_kwh":       round(acond_wh / 1000.0, 1),     # air-con energy (split out of load)
        "ess_other_kwh":   round(ess_other_wh / 1000.0, 1), # clean essentials = load − HW − A/C
        "full_hours":      round(full_s / 3600.0, 1),
        "days":            len(days),
        "coverage_h":      round(span_s / 3600.0, 1),
        # --- curtailment (Phase A) ---
        "curt_hours":      round(curt_s / 3600.0, 1),
        "curt_pct":        round(100.0 * curt_s / day_s) if day_s > 0 else None,  # % of tracked daylight
        "daylight_h":      round(day_s / 3600.0, 1),
        "curt_track_h":    round(curt_track_s / 3600.0, 1),
        "curt_trackers":   {"m288": round(ct["288"] / 3600.0, 1), "m289": round(ct["289"] / 3600.0, 1),
                            "rs1": round(ct["rs1"] / 3600.0, 1), "rs2": round(ct["rs2"] / 3600.0, 1)},
        "foregone_kwh":    round(forgone_wh / 1000.0, 1),
    }


def daily_series():
    """Compact per-day series for the detail-card Week/Month/Year axis — one row per retained
    local day (oldest→newest), display-only daily aggregates. ≤ARCHIVE_DAYS small rows."""
    out = []
    for k in sorted(weekly.keys()):
        b = _ensure_keys(weekly[k])
        out.append({
            "d":           k,
            "solar_kwh":   round(b["pv_wh"] / 1000.0, 2),
            "dc_kwh":      round(b["pv_dc_wh"] / 1000.0, 2),
            "fron_kwh":    round(b["pv_fron_wh"] / 1000.0, 2),
            "sur_kwh":     round(b["sur_wh"] / 1000.0, 2),
            "load_kwh":    round(b["ac_wh"] / 1000.0, 2),
            "acond_kwh":   round(b["acond_wh"] / 1000.0, 2),     # air-con (split out of load_kwh)
            "ess_other_kwh": round(b["ess_other_wh"] / 1000.0, 2),  # clean essentials
            "hw_kwh":      round(b["hw_wh"] / 1000.0, 2),
            "hw_h":        round(b["hw_on_s"] / 3600.0, 2),
            "ac_h":        round(b["ac_on_s"] / 3600.0, 2),
            "avg_soc":     round(b["soc_sum"] / b["soc_n"], 1) if b["soc_n"] else None,
            "min_soc":     round(b["soc_min"]) if b["soc_min"] is not None else None,
            "max_dis_w":   round(-b["batt_min"]) if (b["batt_min"] is not None and b["batt_min"] < 0) else 0,
            "full_h":      round(b["full_s"] / 3600.0, 2),
            "curt_h":      round(b["curt_s"] / 3600.0, 2),
            "day_h":       round(b["day_s"] / 3600.0, 2),
            "forgone_kwh": round(b["forgone_wh"] / 1000.0, 2),
        })
    return out


def seed_weekly_from_history():
    """Best-effort: replay the retained 24 h history samples into the weekly buckets
    so the 'This week' cards show real data immediately on a FRESH start instead of
    integrating up from zero. Only runs when weekly is empty (a restart that loaded
    weekly.json keeps its accumulated days — no double counting)."""
    if weekly:
        return
    prev = None
    for s in samples:
        t = s.get("ts"); hw = s.get("hw")
        if t and isinstance(hw, dict) and prev and 0 < (t - prev) <= ACCUM_MAX_DT_MS:
            _accum_into(weekly.setdefault(_day_key(t), _fresh_bucket()), (t - prev) / 1000.0, t, hw, None, None, False)
        prev = t
    _prune_weekly(now_ms())


def load_weekly():
    """Survive restarts: reload the per-day buckets. Reset the integration clock so
    the first live tick after a restart doesn't bridge the downtime."""
    global weekly
    try:
        with open(WEEK_FILE) as f:
            d = json.load(f)
        days = d.get("days")
        weekly = days if isinstance(days, dict) else {}
    except Exception:
        weekly = {}
    for b in weekly.values():           # migrate older buckets to carry the curtailment keys
        if isinstance(b, dict):
            _ensure_keys(b)
    _accum["ts"] = 0
    _prune_weekly(now_ms())


def now_ms():
    return int(time.time() * 1000)


def pick(payload, allow):
    if not isinstance(payload, dict):
        return None
    return {k: payload[k] for k in payload if k in allow}


# ---- inside-temp enrichment --------------------------------------------------
# The inside-temperature gate is fed by a Tuya CLOUD poll straight into Node-RED
# flow context (it never publishes on MQTT). Read it from the admin context API
# so the dashboard's temp gate shows the real value instead of "feed missing".
_CTX_URL = "https://%s:1881/context/flow/acv15.tab" % BROKER_HOST
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE
_temp_cache = {"val": None, "ts": 0}   # last good (celsius, epoch ms)
INSIDE_STALE_MS = 10 * 60 * 1000       # matches dispatcher's 10-min fail-closed

# ---- PV split (pv_dc / pv_fronius) enrichment --------------------------------
# The dispatcher computes pv_dc + pv_fronius internally (its agg_cache) but does
# NOT publish them on bsf/hotwater/status (only pv_total). For the dashboard's
# DC-MPPT vs Fronius split (issue F / v5 diagram) we read agg_cache READ-ONLY from
# the same Node-RED flow-context API used for inside_temp above. This is DISPLAY-
# ONLY: no dispatcher edit, no MQTT publish — we only read a cache the dispatcher
# already keeps, so ac_load / pv_total / surplus_now math is untouched.
_AGG_URL = "https://%s:1881/context/flow/hwv3.tab" % BROKER_HOST
_pv_cache = {"pv_dc": None, "pv_dc_ts": 0, "pv_fronius": None, "pv_fronius_ts": 0}
PV_STALE_MS = 5 * 60 * 1000            # fail-closed: drop a PV split value older than this

# ---- per-DC-charger live readings (Phase 3) ---------------------------------
# The dashboard's DC-MPPT drill-down wants per-charger watts as
#   hw.chargers = {m288, m289, rs1, rs2}.
# We do NOT use the dispatcher's own mppt288/289_power: those nodes are bound to
# non-existent solarcharger instances 258/259 and read a permanent 0, and wiring
# the real instances into the dispatcher would activate its dormant mppt_curtailed
# branch (a dispatch behaviour change Steven has ruled out). So we read the REAL
# instances straight off the Cerbo's native Victron MQTT tree, READ-ONLY:
#   MPPT-288  N/<portal>/solarcharger/288/Yield/Power
#   MPPT-289  N/<portal>/solarcharger/289/Yield/Power
#   RS Solar1 N/<portal>/multi/0/Pv/0/P   (Pv/0/Name = "Solar 1")
#   RS Solar2 N/<portal>/multi/0/Pv/1/P   (Pv/1/Name = "Solar 2")
# These topics are SILENT unless a client sends R/<portal>/keepalive, so the relay
# keepalive-s every writer cycle (<60 s) to keep them flowing. Payloads are Victron
# JSON: {"value": <number|null>}. Paths + format verified live (dossiers/data_layer_map.md).
VICTRON_PORTAL = _HW.get("vrm_portal_id", "YOUR_VRM_PORTAL_ID")
# Per-charger LIVE-reading topics, built from config.hardware.chargers (any count).
# kind 'solarcharger' -> N/<portal>/solarcharger/<instance>/Yield/Power
# kind 'multi'        -> N/<portal>/multi/<instance>/Pv/<tracker>/P
# Defaults to this farm's 288/289 + RS1/RS2 layout when none are configured.
def _build_native_map(chargers, portal):
    m = {}
    for c in (chargers or []):
        if not isinstance(c, dict): continue
        key = c.get("key");  inst = c.get("instance")
        if not key or inst is None: continue
        if c.get("kind") == "solarcharger":
            m["N/%s/solarcharger/%s/Yield/Power" % (portal, inst)] = key
        elif c.get("kind") == "multi":
            m["N/%s/multi/%s/Pv/%s/P" % (portal, inst, c.get("tracker", 0))] = key
    return m

_NATIVE_MAP = _build_native_map(_HW.get("chargers"), VICTRON_PORTAL) or {
    "N/%s/solarcharger/288/Yield/Power" % VICTRON_PORTAL: "m288",
    "N/%s/solarcharger/289/Yield/Power" % VICTRON_PORTAL: "m289",
    "N/%s/multi/0/Pv/0/P" % VICTRON_PORTAL:               "rs1",
    "N/%s/multi/0/Pv/1/P" % VICTRON_PORTAL:               "rs2",
}
KEEPALIVE_TOPIC  = "R/%s/keepalive" % VICTRON_PORTAL
CHARGER_STALE_MS = 90 * 1000           # fail-closed: a per-charger value older than this -> None ("—")
_charger_cache = {}                    # key -> {"value": W, "ts": ms}
_charger_lock  = threading.Lock()
_mqtt = {"client": None}               # set in main(); used to publish the read-request keepalive


def update_charger(topic, payload):
    """Cache one native Victron per-charger reading. READ-ONLY — just stores it."""
    key = _NATIVE_MAP.get(topic)
    if key is None:
        return
    v = payload.get("value") if isinstance(payload, dict) else payload
    try:
        v = float(v) if v is not None else None
    except (TypeError, ValueError):
        v = None
    with _charger_lock:
        _charger_cache[key] = {"value": v, "ts": now_ms()}


def build_chargers(t):
    """Per-charger watts for the dashboard drill-down: {m288, m289, rs1, rs2}.
    A value older than CHARGER_STALE_MS (or never seen) becomes None so the
    dashboard honestly shows "—" rather than a stale figure. Returns None until at
    least one fresh reading exists (the dashboard then keeps its Phase-3 banner)."""
    out, any_fresh = {}, False
    with _charger_lock:
        for key in ("m288", "m289", "rs1", "rs2"):
            e = _charger_cache.get(key)
            if e and e.get("value") is not None and (t - e["ts"]) <= CHARGER_STALE_MS:
                out[key] = round(e["value"]); any_fresh = True
            else:
                out[key] = None
    return out if any_fresh else None


def send_keepalive():
    """Publish the Victron read-request so the broker republishes its N/ tree
    (silent otherwise). Empty payload, R/ prefix = read-only; never a W/ write."""
    c = _mqtt.get("client")
    if c is None:
        return
    try:
        c.publish(KEEPALIVE_TOPIC, "")
    except Exception:
        pass


def _ctx_val(node):
    """Node-RED context values look like {'msg': '27.9', 'format': 'number'}."""
    if isinstance(node, dict):
        node = node.get("msg")
    return node


def fetch_inside_temp():
    try:
        with urllib.request.urlopen(_CTX_URL, timeout=3, context=_SSL) as r:
            d = json.loads(r.read().decode())
        mem = d.get("memory", d)
        v = _ctx_val(mem.get("ac_inside_temp"))
        ts = _ctx_val(mem.get("ac_inside_temp_ts"))
        if v is not None and ts is not None:
            _temp_cache["val"] = float(v)
            _temp_cache["ts"] = int(float(ts))
    except Exception:
        pass
    return _temp_cache["val"], _temp_cache["ts"]


def fetch_pv_split():
    """READ-ONLY: read the dispatcher's agg_cache (pv_dc / pv_fronius) from the
    Node-RED flow context. agg_cache is a JSON string of {key:{ts,value}}; we only
    read it. No dispatcher change, no MQTT publish, no keepalive."""
    try:
        with urllib.request.urlopen(_AGG_URL, timeout=3, context=_SSL) as r:
            d = json.loads(r.read().decode())
        mem = d.get("memory", d)
        raw = _ctx_val(mem.get("agg_cache"))
        agg = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(agg, dict):
            for key in ("pv_dc", "pv_fronius"):
                e = agg.get(key)
                if isinstance(e, dict) and e.get("value") is not None:
                    _pv_cache[key] = e["value"]
                    _pv_cache[key + "_ts"] = int(e.get("ts") or now_ms())
    except Exception:
        pass
    return _pv_cache


# ---- per-plug power attribution (Tuya cloud, READ-ONLY) ----------------------
# The Cerbo reports household loads as ONE lumped "ac_load" bucket; when the hot
# water element or air-con switches on, its draw is hidden INSIDE that number
# (the dispatcher's own plug_power telemetry is dead — no Tuya source feeds it).
# We poll the two big intermittent loads straight from the Tuya cloud — the same
# read-only account the inside-temp gate already uses — and break them out so the
# dashboard shows Hot Water / Air-con / Other-essential honestly instead of one
# ballooning figure. GET-only: this NEVER sends a device command. The keys below
# are the same read-only creds already in the deployed Node-RED flow; they are
# NEVER written to the public snapshot — only the resulting watts leave the farm.
import hmac, hashlib

TUYA_CID    = _TUYA.get("access_id", "YOUR_TUYA_ACCESS_ID")
TUYA_SECRET = _TUYA.get("access_secret", "YOUR_TUYA_ACCESS_SECRET")
TUYA_HOST   = _TUYA.get("api_host", "https://openapi.tuyaus.com")  # neutral default; set your region in config.json
TUYA_EMPTY_SHA = hashlib.sha256(b"").hexdigest()
PLUGS = {            # category -> Tuya deviceId (cur_power unit W, scale 1 -> /10)
    "hw": _load_dev("hot_water"),     # metered hot-water element (from loads[] role)
    "ac": _load_dev("air_con"),       # metered air-conditioner (from loads[] role)
}
PLUG_ALIVE_MS  = 10 * 60 * 1000   # cur_voltage heartbeat older than this => plug telemetry dead
MISMATCH_TOL_W = 200              # essential_other below -this => attribution_mismatch flag
_tuya = {"token": None, "token_exp": 0}


def _tuya_sign(s):
    return hmac.new(TUYA_SECRET.encode(), s.encode(), hashlib.sha256).hexdigest().upper()


def _tuya_get(path, token=None):
    t = str(now_ms())
    sts = f"GET\n{TUYA_EMPTY_SHA}\n\n{path}"
    sign = _tuya_sign(TUYA_CID + (token or "") + t + sts)
    hdr = {"client_id": TUYA_CID, "sign": sign, "sign_method": "HMAC-SHA256", "t": t, "lang": "en"}
    if token:
        hdr["access_token"] = token
    req = urllib.request.Request(TUYA_HOST + path, headers=hdr, method="GET")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def _tuya_token():
    if _tuya["token"] and now_ms() < _tuya["token_exp"]:
        return _tuya["token"]
    d = _tuya_get("/v1.0/token?grant_type=1")
    res = d.get("result") or {}
    _tuya["token"] = res.get("access_token")
    _tuya["token_exp"] = now_ms() + int(res.get("expire_time", 7200) - 120) * 1000
    return _tuya["token"]


def poll_plugs():
    """Read per-plug power for the metered loads. READ-ONLY (GET shadow only).

    Returns {cat: {w, state, age_s, metered}}:
      w        watts now (None when not metered); for a steady element this is the
               last-reported cur_power — these plugs only re-report on CHANGE, so a
               constant element's value goes stale-but-correct (don't discard it).
      state    "on" | "off" | "stale"  (from switch_1 + liveness, NOT cur_power age)
      age_s    age of the cur_power reading, surfaced raw so the dashboard is honest
      metered  False => plug telemetry is dead; caller MUST NOT subtract it.
    """
    out = {}
    try:
        tok = _tuya_token()
    except Exception:
        return out
    t = now_ms()
    for cat, dev in PLUGS.items():
        try:
            d = _tuya_get(f"/v2.0/cloud/thing/{dev}/shadow/properties", tok)
            props = {p["code"]: p for p in d.get("result", {}).get("properties", [])}
            sw = props.get("switch_1", {})
            cp = props.get("cur_power", {})
            cv = props.get("cur_voltage", {})
            on = bool(sw.get("value"))
            # Liveness: cur_voltage refreshes ~every 2 min regardless of load, so it
            # is the heartbeat. cur_power only re-reports on change (a steady element
            # stops re-reporting), so gating on cur_power age would falsely mark a
            # still-heating element "stale" — never do that.
            hb_age = (t - cv.get("time", 0)) if cv.get("time") else 10**12
            alive = hb_age <= PLUG_ALIVE_MS
            cp_w = round(cp["value"] / 10.0) if cp.get("value") is not None else None  # scale 1
            val_age = round((t - cp.get("time", t)) / 1000.0, 1) if cp.get("time") else None
            if not alive:
                # heartbeat lapsed: the Tuya cloud still returns the LAST cur_power +
                # switch, so surface them as a HELD reading (metered=False). The caller
                # decides whether to trust the held watts (variable loads like the A/C)
                # or substitute a nominal (the HW element). `on` = last-known switch.
                out[cat] = {"w": cp_w, "state": "stale", "on": on, "age_s": val_age, "metered": False}
            elif on:
                out[cat] = {"w": cp_w if cp_w is not None else 0, "state": "on", "on": True,
                            "age_s": val_age, "metered": True}
            else:   # switch off => no draw possible, regardless of last cur_power value
                out[cat] = {"w": 0, "state": "off", "on": False, "age_s": val_age, "metered": True}
        except Exception:
            out[cat] = {"w": None, "state": "stale", "on": False, "age_s": None, "metered": False}
    return out


def attribute_loads(hw, ac, plugs):
    """Break HW + air-con out of the lumped Cerbo essential bucket (hw.ac_load).

    essential_other_w = ac_load - hot_water_w - ac_w, clamped >=0. Both loads use a
    stable, anti-flip attribution so a bursty plug meter can't make the figure toggle
    buckets (see the per-load comments). A net-negative result (meters disagree) clamps
    to 0 and raises attribution_mismatch."""
    base = None
    if hw is not None and hw.get("ac_load") is not None:
        try:
            base = round(float(hw["ac_load"]))
        except Exception:
            base = None
    hwL, acL = plugs.get("hw"), plugs.get("ac")
    if base is None and not hwL and not acL:
        return None
    hw_metered_w = (hwL or {}).get("w")
    ac_metered_w = (acL or {}).get("w")
    hw_metered = bool(hwL and hwL.get("metered") and hw_metered_w is not None)
    ac_metered = bool(acL and acL.get("metered") and ac_metered_w is not None)
    # Both Tuya plugs report cur_power AND their cur_voltage heartbeat together in bursts
    # then go silent, so the 10-min liveness gate intermittently marks a plug "stale"
    # mid-run (the heartbeat is NOT the independent 2-min beat the gate assumes). Dropping
    # the load out of the breakdown when that happens lumps its draw straight back into
    # Essentials and the figure visibly FLIPS bucket every burst gap (the "flip-flop" bug).
    # So when a load is commanded ON but its meter is stale, keep attributing it:
    #   HW  — resistive element on a relay => element nominal (HW_NOMINAL_W); the weekly
    #         accumulator already uses this exact fallback.
    #   A/C — a Daikin inverter MODULATES (no fixed rating fits), so HOLD its last reported
    #         watts (the Tuya cloud still returns them) rather than guess a rating.
    # Raw state/age/metered + an explicit *_estimated flag are still published so the
    # dashboard shows the estimate honestly (marked ≈), never as a live meter.
    hw_on = (hw or {}).get("hwState") == "on"
    hw_estimated = False
    if hw_metered:
        hw_w, hw_sub = hw_metered_w, hw_metered_w
    elif hw_on:
        hw_w, hw_sub, hw_estimated = HW_NOMINAL_W, HW_NOMINAL_W, True
    else:
        hw_w, hw_sub = hw_metered_w, 0       # off / never-seen: shown raw (None or 0)
    ac_on = ((ac or {}).get("acState") == "on") or bool(acL and acL.get("on"))
    ac_estimated = False
    if ac_metered:
        ac_w, ac_sub = ac_metered_w, ac_metered_w
    elif ac_on and ac_metered_w is not None:
        ac_w, ac_sub, ac_estimated = ac_metered_w, ac_metered_w, True   # hold last reported draw
    else:
        ac_w, ac_sub = ac_metered_w, 0       # off / never-seen: shown raw (None or 0)
    mismatch, ess_other = False, None
    if base is not None:
        raw_other = base - hw_sub - ac_sub
        mismatch = raw_other < -MISMATCH_TOL_W
        ess_other = max(0, round(raw_other))
    return {
        "essential_total_w": base,            # raw lumped Cerbo bucket (shown raw)
        "essential_other_w": ess_other,       # bucket minus HW + air-con
        "hot_water_w":       hw_w,            # metered when fresh, element nominal when estimated
        "hot_water_state":   (hwL or {}).get("state", "unknown"),
        "hot_water_age_s":   (hwL or {}).get("age_s"),
        "hot_water_metered": hw_metered,
        "hot_water_estimated": hw_estimated,  # True => element-rating stand-in (meter stale, HW commanded on)
        "ac_w":              ac_w,            # metered when fresh, last-held draw when estimated
        "ac_state":          (acL or {}).get("state", "unknown"),
        "ac_age_s":          (acL or {}).get("age_s"),
        "ac_metered":        ac_metered,
        "ac_estimated":      ac_estimated,    # True => held-last-draw stand-in (meter stale, A/C commanded on)
        "attribution_mismatch": mismatch,
        "source": "tuya-cloud-ro",
    }


def load_existing():
    """Survive restarts: re-seed history from disk so the 24 h view isn't reset."""
    global samples, events
    try:
        with open(HIST_FILE) as f:
            d = json.load(f)
        cut = now_ms() - HIST_MAX_AGE_MS
        samples = [s for s in d.get("samples", []) if s.get("ts", 0) >= cut][-HIST_MAX:]
        events = [e for e in d.get("events", []) if e.get("ts", 0) >= cut][-EVENTS_MAX:]
    except Exception:
        samples, events = [], []


def atomic_write(path, obj):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        try: os.remove(tmp)
        except Exception: pass


def on_connect(client, userdata, flags, rc):
    _mqtt["client"] = client
    for t, q in TOPICS:
        client.subscribe(t, q)
    for t in _NATIVE_MAP:            # native Victron per-charger topics (Phase 3)
        client.subscribe(t, 0)
    send_keepalive()                # kick the N/ tree so per-charger data starts flowing
    print(f"[publisher] connected rc={rc}, subscribed {len(TOPICS)}+{len(_NATIVE_MAP)} topics", flush=True)


def _parse_iso_ms(iso):
    try:
        return int(time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) * 1000)
    except Exception:
        return now_ms()


def _inject_pv_split(hw, t):
    """Read the dispatcher's pv_dc/pv_fronius split RIGHT NOW (status just arrived) so
    it shares the same dispatcher tick as pv_total — keeps pv_total ≈ pv_dc+pv_fronius
    in the snapshot. Display-only, read-only; never mutates dispatcher state. Must be
    called OUTSIDE the lock (it does network I/O)."""
    if not isinstance(hw, dict):
        return hw
    pv = fetch_pv_split()
    if pv.get("pv_dc") is not None and pv["pv_dc_ts"] and (t - pv["pv_dc_ts"]) <= PV_STALE_MS:
        hw["pv_dc"] = pv["pv_dc"]
    if pv.get("pv_fronius") is not None and pv["pv_fronius_ts"] and (t - pv["pv_fronius_ts"]) <= PV_STALE_MS:
        hw["pv_fronius"] = pv["pv_fronius"]
    return hw


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        return
    t = now_ms()
    topic = msg.topic
    if topic in _NATIVE_MAP:        # native Victron per-charger reading (Phase 3)
        update_charger(topic, payload)
        return
    # HW status: align the pv split to this tick BEFORE taking the lock (network I/O).
    hw_picked = _inject_pv_split(pick(payload, HW_FIELDS), t) if topic == "bsf/hotwater/status" else None
    with lock:
        if topic == "bsf/hotwater/status":
            state["hw"] = hw_picked; state["hw_ts"] = t
        elif topic == "bsf/ac/status":
            state["ac"] = pick(payload, AC_FIELDS); state["ac_ts"] = t
        elif topic == "bsf/presence/state":
            sp = sanitize_presence(payload)
            if sp is not None:
                state["presence"] = sp; state["presence_ts"] = t
        elif topic.endswith("/log") or topic.endswith("/anomaly"):
            src = "hw" if "hotwater" in topic else ("ac" if "/ac/" in topic else "sys")
            warn = topic.endswith("/anomaly")
            ev = pick(payload, EVENT_FIELDS) or {}
            name = str(ev.get("event") or ev.get("reason") or ("anomaly" if warn else "event"))
            ts = _parse_iso_ms(payload["ts"]) if isinstance(payload, dict) and payload.get("ts") else t
            rec = {"ts": ts, "src": src,
                   "kind": ("on" if name.lower().startswith("on") else
                            "off" if name.lower().startswith("off") else
                            "warn" if warn else "evt"),
                   "name": name, "detail": _summ(ev)}
            events.append(rec)
            _prune_events()


def _summ(ev):
    bits = []
    for k in ("soc", "surplus", "ac", "batt_power"):
        if ev.get(k) is not None:
            bits.append(f"{k} {ev[k]}")
    return " · ".join(bits)


def _prune_events():
    global events
    cut = now_ms() - HIST_MAX_AGE_MS
    events = [e for e in events if e["ts"] >= cut][-EVENTS_MAX:]


def writer_loop():
    global _last_sample, samples
    while True:
        time.sleep(WRITE_EVERY_S)
        t = now_ms()
        send_keepalive()                           # keep the native N/ tree flowing (read-only)
        temp_val, temp_ts = fetch_inside_temp()   # network call — outside the lock
        plugs = poll_plugs()                       # Tuya cloud read — outside the lock
        chargers = build_chargers(t)               # per-charger watts — outside the lock
        with lock:
            hw, ac = state["hw"], state["ac"]
            loads = attribute_loads(hw, ac, plugs)
            # NB: pv_dc / pv_fronius are injected into state["hw"] in on_message at
            # status-arrival time (so they share the same dispatcher tick as pv_total) —
            # see _inject_pv_split. writer_loop just relays whatever hw already carries.
            if ac is not None and temp_val is not None and temp_ts:
                ac = dict(ac)   # copy so we don't mutate the cached status
                age_s = round((t - temp_ts) / 1000, 1)
                ac["inside_temp"] = temp_val
                ac["inside_temp_age_s"] = age_s
                ac["inside_temp_usable"] = (t - temp_ts) <= INSIDE_STALE_MS
            # Per-charger drill-down (Phase 3): attach to a COPY of hw so the public
            # snapshot carries hw.chargers (read live off the native Victron MQTT tree)
            # without bloating the history samples below.
            hw_out = hw
            if hw is not None and chargers is not None:
                hw_out = dict(hw); hw_out["chargers"] = chargers
            hw_age = round((t - state["hw_ts"]) / 1000, 1) if state["hw_ts"] else None
            ac_age = round((t - state["ac_ts"]) / 1000, 1) if state["ac_ts"] else None
            # 7-day weekly accumulators (display-only). Wrapped so a fault here can
            # NEVER stop the core snapshot writing below — the relay keeps serving
            # live data; the weekly cards just stay empty if this ever breaks.
            wk_summary, wk_out = None, None
            try:
                hw_fresh = bool(state["hw_ts"]) and (t - state["hw_ts"]) <= HW_FRESH_MS
                accum_weekly(t, hw, hw_fresh, loads, chargers)
                wk_summary = weekly_summary(t)
                wk_out = {"days": weekly, "accum_ts": _accum["ts"], "ts": t}
            except Exception:
                wk_summary, wk_out = None, None
            presence = state["presence"]
            presence_age = round((t - state["presence_ts"]) / 1000, 1) if state["presence_ts"] else None
            snap = {
                "ok": bool(hw or ac),
                "ts": t,
                "hw": hw_out, "ac": ac,
                "hw_age_s": hw_age, "ac_age_s": ac_age,
                "loads": loads,
                "weekly": wk_summary,
                "presence": presence, "presence_age_s": presence_age,
                "source": "cerbo-mqtt-relay",
            }
            if t - _last_sample >= SAMPLE_EVERY_MS and (hw or ac):
                samples.append({"ts": t, "hw": hw, "ac": ac})
                cut = t - HIST_MAX_AGE_MS
                samples = [s for s in samples if s["ts"] >= cut][-HIST_MAX:]
                _last_sample = t
            try:
                daily = daily_series()      # per-day archive for detail-card Week/Month/Year
            except Exception:
                daily = []
            hist = {"samples": samples, "events": events, "daily": daily}
        atomic_write(STATE_FILE, snap)
        atomic_write(HIST_FILE, hist)
        if wk_out is not None:
            atomic_write(WEEK_FILE, wk_out)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    load_existing()
    load_weekly()                 # restore the 7-day buckets (if any)
    seed_weekly_from_history()    # fresh start only: backfill the week from the 24 h history
    threading.Thread(target=writer_loop, daemon=True).start()
    while True:  # outer reconnect loop — never die
        try:
            c = mqtt.Client(client_id="bsf-solar-relay-ro")
            c.on_connect = on_connect
            c.on_message = on_message
            _mqtt["client"] = c
            c.connect(BROKER_HOST, BROKER_PORT, keepalive=30)
            c.loop_forever()   # blocks; auto-reconnects internally
        except Exception as e:
            print(f"[publisher] connection error: {e}; retry in 5s", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
