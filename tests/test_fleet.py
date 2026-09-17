#!/usr/bin/env python3
"""Fleet relay tests: whitelist copy, staleness, offline. Run: python3 tests/test_fleet.py"""
import json, os, sys, importlib.util
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec=importlib.util.spec_from_file_location("fleet", os.path.join(ROOT,"fleet/fleet_relay.py")); fleet=importlib.util.module_from_spec(spec); spec.loader.exec_module(fleet)
fails=0
def check(n,c):
    global fails; print(("  ok " if c else "  FAIL ")+n); fails+=0 if c else 1
site={"key":"x","name":"X","state_url":"http://x/state.json","dashboard_url":"http://x/d.html"}
now=1_700_000_000_000
snap={"ok":True,"ts":now-20_000,"hw_age_s":5,"hw":{"soc":81,"pv_total":6400,"ac_load":900,"batt_power":1200,"curtailed":True,"hwState":"on"},
      "ac":{"acState":"off","ac_mode":"AUTO","inside_temp":21.5},"loads":{"hot_water_w":2400},"today":{"hw_kwh":3.2,"ac_kwh":0,"hw_h":1.4},
      "presence":{"steven":{"home":True,"last_seen_ms":1}},"tuya_lan":{"hot_water":{"device_id":"abc","local_key":"secret"}},"source":"cerbo-mqtt-relay"}
c=fleet.summarise(site,snap,now)
check("fields copied", c["soc"]==81 and c["pv_w"]==6400 and c["load_w"]==900 and c["batt_w"]==1200 and c["hw_state"]=="on" and c["ac_mode"]=="AUTO" and c["today_hw_kwh"]==3.2)
check("state ok at 20 s", c["state"]=="ok" and c["age_s"]==20.0)
check("presence + tuya secrets NOT copied", "presence" not in json.dumps(c) and "secret" not in json.dumps(c) and "abc" not in json.dumps(c))
check("dashboard url passed through", c["dashboard_url"]=="http://x/d.html")
lag=dict(snap, ts=now-300_000); check("300 s old => lagging", fleet.summarise(site,lag,now)["state"]=="lagging")
old=dict(snap, ts=now-700_000); check("700 s old => offline", fleet.summarise(site,old,now)["state"]=="offline")
hwlag=dict(snap, hw_age_s=400); check("hw feed 400 s old => lagging even if relay fresh", fleet.summarise(site,hwlag,now)["state"]=="lagging")
notok=dict(snap, ok=False); check("relay ok:false => offline", fleet.summarise(site,notok,now)["state"]=="offline")
o=fleet.offline(site,"<urlopen error timed out>"); check("offline card keeps name/url, friendly error", o["state"]=="offline" and o["name"]=="X" and o["error"].startswith("no response") and o["soc"] is None)
garbage=fleet.summarise(site,{"ok":True,"ts":"nope","hw":{"soc":"x"}},now); check("garbage => offline, no crash", garbage["state"]=="offline" and garbage["soc"] is None)
dead=dict(snap, hw=dict(snap["hw"], stale=["soc","pv_fronius","ac_load"]))
d=fleet.summarise(site,dead,now); check("stale inverter inputs => offline, numbers withheld", d["state"]=="offline" and d["soc"] is None and d["pv_w"] is None and "no live readings" in d["error"])
ok_stale=dict(snap, hw=dict(snap["hw"], stale=[])); check("empty stale list => still live", fleet.summarise(site,ok_stale,now)["state"]=="ok")
# --- feedback inbox ---
st={"key":"trish","name":"Trish","state_url":"http://pi:8780/state.json"}
check("feedback url derived from state url", fleet.feedback_url(st)=="http://pi:8780/feedback.json")
check("no feedback url for a non-standard state url", fleet.feedback_url({"key":"f","state_url":"https://x/solar/state"}) is None)
raw={"id":"1789650362413-0405027a","text":"  cold water  ","kind":"change","name":"Mum","received_ts":1789650362413,
     "context":{"soc":97.5,"stale":["soc"],"evil":"<script>","page":"/d.html"},"site":"SPOOFED"}
n=fleet.clean_note(st,raw)
check("note cleaned: text trimmed, site comes from OUR list not the payload", n["text"]=="cold water" and n["site"]=="Trish" and n["key"]=="trish/1789650362413-0405027a")
check("note context whitelisted", "evil" not in n["context"] and n["context"]["soc"]==97.5)
check("junk notes dropped", fleet.clean_note(st,{"id":"x"}) is None and fleet.clean_note(st,"nope") is None and fleet.clean_note(st,{"id":"a"*41,"text":"t"}) is None)
inbox=[]; check("merge adds new notes once", fleet.merge_inbox(inbox,st,{"notes":[raw]})==1 and fleet.merge_inbox(inbox,st,{"notes":[raw]})==0 and len(inbox)==1)
other={"key":"dad","name":"Dad","state_url":"http://d:8780/state.json"}
newer=dict(raw, received_ts=raw["received_ts"]+5000, text="newer")
check("same note id at another site is a different note; newest first", fleet.merge_inbox(inbox,other,{"notes":[newer]})==1 and inbox[0]["site"]=="Dad" and len(inbox)==2)
check("garbage payload tolerated", fleet.merge_inbox(inbox,st,None)==0 and fleet.merge_inbox(inbox,st,{"notes":"x"})==0)
print("\n%d failure(s)"%fails); sys.exit(1 if fails else 0)
