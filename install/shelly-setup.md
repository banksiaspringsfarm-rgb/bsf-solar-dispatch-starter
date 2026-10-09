# Shelly relay for the hot water (no Tuya needed)

A Shelly, or a "Powered by Shelly" relay such as the **Ogemray 25A (SW40)**, can switch the hot-water load
instead of a Tuya plug. It is controlled over the site's own MQTT broker (the Pi's Mosquitto, or the Cerbo's),
so there is no cloud account, no developer project and no local key. Any Shelly with **Gen2 or newer firmware**
(Plus / Pro / Gen3 / Gen4 / Powered-by-Shelly) works. Gen1 (Shelly 1, 1PM original) does not.

Today only the `hot_water` load can be a Shelly.

## What the dispatcher does with it

- **ON** is sent with a **dead-man timer** (`toggle_after`). The relay switches itself OFF `deadman_s` seconds
  (default 300) after the dispatcher last confirmed it should be on. The dispatcher re-confirms every 30 s, and
  each re-confirm restarts the timer (measured on an Ogemray 25A, firmware 2.0.1: renewed at 60 s, self-OFF at 180 s).
  If the Pi, Node-RED or the Wi-Fi dies, the element is off within `deadman_s`.
- On every deploy and Node-RED start, the relay's **power-on state is set to OFF**. A power cut never brings
  the element back on by itself.
- The relay is asked for its state every 30 s, and it also reports changes as they happen. Its own power meter
  feeds the dashboard. If it has no meter, `rated_w` stands in while it is ON.
- Manual presses on the relay's own button:
  - **ON while the dispatcher has it OFF:** left alone inside the daytime window. Outside it, the lockout loop
    forces it OFF within 5 minutes, the same as with a Tuya plug.
  - **OFF while the dispatcher has it ON:** switched back ON within 30 s by the dead-man refresh.

  To keep the element off while there's surplus, use its breaker or isolator. That is the only safe way
  before any work on the element.

## 1. On the bench (before the electrician)

1. Power the relay from a lead on the bench, or a test board. **Not** in the switchboard yet.
2. Add it in the **Shelly Smart Control** app on the site's 2.4 GHz Wi-Fi. If it's for another site, set it up
   at that site or add that site's Wi-Fi as well.
3. **Update the firmware** (app → device → Settings → Firmware).
4. In the device's settings (app, or its web page at `http://<relay-ip>`), open **MQTT**:
   - Enable MQTT.
   - Server: `<the Pi or Cerbo IP>:1883` (no user/password on the Pi's broker).
   - **MQTT prefix**: something short and unique, e.g. `glen-eden-booster1`. This goes in config.json.
   - Leave **RPC status notifications over MQTT** ticked.
   - Save. The relay restarts.
5. Give the relay a fixed address (a DHCP reservation in the router), so you can find its web page later.

Check it answers, from the Pi:

```bash
mosquitto_sub -t 'bench/rpc' -C 1 -v &
mosquitto_pub -t 'glen-eden-booster1/rpc' -m '{"id":1,"src":"bench","method":"Switch.GetStatus","params":{"id":0}}'
```

It should print `"output":false` and, on a metering relay, `"apower":0`.

## 2. config.json

Replace the `hot_water` entry in `loads[]`:

```json
{ "role": "hot_water", "label": "Booster 1", "driver": "shelly",
  "shelly": { "topic_prefix": "glen-eden-booster1", "switch_id": 0, "deadman_s": 300 },
  "rated_w": 3600, "essential": false, "metered": true }
```

- `rated_w`: the element's nameplate watts. Also set `dispatcher.hw_element_w` to the same number.
- `metered`: `true` if the relay measures power (the Ogemray 25A, any *PM* model), otherwise `false`.
- `switch_id`: `0` on a single relay. On a Pro 2/4 it is the channel number.

Then:

```bash
python3 install/deploy.py check
python3 install/deploy.py flow --deploy
python3 install/deploy.py publisher --force
```

`check` must show `hot_water = Shelly over MQTT`, and `flow` must show `Shelly: hot water switches over MQTT RPC`.
In Node-RED a new tab, **Shelly — Hot Water**, appears. Its status dots show the last command sent and the
relay's state and watts.

## Two boosters: a primary and a secondary

A second Shelly element goes in as `role: "hot_water_2"` (its own `topic_prefix`). It is a **secondary**:

- it only ever runs while Booster 1 is on, and only after Booster 1 has run `min_primary_on_ms` (2 min);
- it needs a nearly full battery (`soc_on` 95 %, off below `soc_off` 90 %), and either `surplus_on_w` spare
  after Booster 1 (default: its rating + 600 W) or the panels being throttled (curtailed);
- it sheds first: at once if Booster 1 goes off, the battery discharges more than `batt_trip_w` (2 kW),
  the total load passes `max_total_w`, or it leaves the daytime window; after `sustain_off_ms` if the panels
  stop covering both. After it sheds it waits `min_off_ms` (10 min) before trying again.

The dispatcher sees the two boosters as one hot-water load (their watts added), so Booster 2 can never make it
shed Booster 1. Settings go in `dispatcher.hot_water_2`:

```json
"hot_water_2": { "max_total_w": 10000, "soc_on": 95, "soc_off": 90, "batt_trip_w": 2000 }
```

**`max_total_w` is the decision that matters.** On an AC-coupled site the panels feed loads directly, so the
total can pass the inverter's rating while the sun covers it. But if the panels drop out (cloud, or the
AC-coupled inverters tripping), the inverter carries everything until the dispatcher sheds, within about 10 s.
Set it to what your inverter can carry for that long, not its continuous rating. It defaults to the continuous
rating, which means Booster 2 rarely runs. `safety_cap_w` must be above it, so Booster 2 always sheds before
the safety cap trips Booster 1.

## 3. Install (electrician)

The relay is hardwired: in Australia a licensed electrician fits it. It is a bare module, not weather-rated,
so it goes **inside an enclosure**: the switchboard or an IP-rated box. It must never sit loose in the
isolator. Wiring: supply **L** and **N** in, **L1** (the switched output) to the element. **S1/COM** are
for an optional manual switch. Keep the element's own breaker and thermostat in the circuit. Check the relay's
current rating against the element: a 3.6 kW element draws about 15 A on a 25 A relay.

## 4. Verify on site

- On the dashboard, the hot-water tile shows **OFF** with 0 W, not "not connected".
- On a sunny morning with a full battery, it switches ON, the tile shows the relay's watts, and it stays on.
- `mosquitto_sub -t 'bsf/hotwater/#' -v` shows `plug_state` and `plug_power` every 30 s.

## Setting one up without the app

A fresh relay broadcasts an open hotspot (`ShellyXXX-…` / `Ogemray25A-<MAC>`) with its API at `192.168.33.1`.
From a computer joined to that hotspot, `POST http://192.168.33.1/rpc` with `Sys.SetConfig` (device name) and
`WiFi.SetConfig`. For an **open** home network the firmware still demands a password field: send
`"sta":{"ssid":"…","is_open":true,"pass":"","enable":true}`, or it answers `-103 Pass field required`.

## Bench test without hardware

`tests/e2e/run_shelly_e2e.py` runs the real flow in a throwaway Node-RED against a simulated relay
(`tests/e2e/shelly_bench.js`), including killing Node-RED to prove the dead-man. Run it in daylight
(05:30–15:30 AEST), because the dispatcher only switches then.
