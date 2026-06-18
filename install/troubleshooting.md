# Troubleshooting

Symptom → cause → fix. Tuya errors first (they're the fiddly bit), then the rest of the install.

**Whenever you're stuck: paste the exact error into Claude Code and it'll diagnose.** The error
text — code numbers, the failing host, the log line — is what makes diagnosis fast, so copy it
verbatim rather than paraphrasing.

---

## Tuya errors

### Error 1004 — "sign invalid"

The request signature didn't check out. Three common causes:

- **Wrong Access ID / Access Secret** — re-copy both from the project **Overview → Authorization
  Key** (walkthrough §6). The Secret is easy to truncate; copy the whole thing.
- **System clock skew** — Tuya signatures embed a millisecond timestamp. If your machine's clock
  is off by more than a few seconds, every request fails as "sign invalid."
  **Fix:** sync the clock via NTP.
  - macOS: enable "Set time and date automatically" in System Settings → General → Date & Time
    (or `sudo sntp -sS time.apple.com`).
  - Cerbo / Linux: make sure NTP is enabled and the time is right.
- **Wrong region host** — `tuya.api_host` points at a different DC than the one issuing the keys.
  Match it to your Data Center (`region-datacenter-map.md`).

### Error 1106 — "permission deny"

You authenticated, but you're not allowed to do that. Causes:

- **A required API pack isn't subscribed/authorized** — check IoT Core + Authorization Token
  Management + Smart Home Basic Service (+ Device Status Notification) are subscribed **and**
  authorized for **this project** (walkthrough §4).
- **App account not linked** — you never completed the QR link, or it didn't take
  (walkthrough §5).
- **Data-center mismatch** — project DC ≠ app account's home DC. Most common on Australia
  (try `eu`, fall back to `us-e`). See `region-datacenter-map.md`.

### "token invalid" / "token expired"

- **Authorization Token Management pack not subscribed**, or
- **Free trial lapsed** so the token endpoint stopped working.

**Fix:** subscribe Authorization Token Management, and **Extend Trial Period**
(Cloud → Cloud Services → IoT Core → My Subscriptions → Extend Trial Period). See also 28841002.

### "No devices returned" / "All Devices" is empty

The #1 install failure.

- **Wrong Data Center** — the project DC doesn't match where your devices actually live. For
  Australia, try `eu` first, then `us-e`. Change the project DC and **re-link the app account**.
- **Account not actually linked** — the QR scan didn't complete. Redo walkthrough §5 and confirm
  the **Confirm** tap on the phone.

See `region-datacenter-map.md` → "How to confirm".

### Error 2406 — "skill id invalid"

- **Wrong phone app** — you linked an account from a **vendor / OEM-branded** app instead of the
  generic **Smart Life** or **Tuya Smart** app. Use Smart Life / Tuya Smart.
- **Data-center mismatch** (same family as 1106).

**Fix:** in the project, **unlink** the app account and **re-link** it (walkthrough §5), using the
Smart Life / Tuya Smart app and the correct DC.

### Error 28841002

- **Free trial lapsed.**

**Fix:** Cloud → Cloud Services → IoT Core → **My Subscriptions → Extend Trial Period**
(walkthrough §4). Extend it proactively so it doesn't bite again.

---

## Install / runtime issues (non-Tuya)

### Cerbo Node-RED unreachable

`deploy.py` can't reach Node-RED on the Cerbo.

- **Wrong `cerbo_ip`** in `config.json` — confirm the Cerbo's IP.
- **Not on the same LAN** — the machine running `deploy.py` must be on the same network as the
  Cerbo.
- **Quick check:** open **`https://<cerbo>:1881`** in a browser. If the Node-RED editor loads,
  the network's fine.
- **Node-RED admin auth is enabled** — if the editor demands a login, the automated deploy can't
  push the flow. In that case, **manually import** the generated flow:
  in the Node-RED editor → **menu → Import** → load
  **`.build/bsf-solar-dispatch.deployed.flow.json`** → **Deploy**.

### Publisher service not writing `state.json`

The dashboard has no data because the publisher isn't producing state.

- **`paho-mqtt` missing** in the python the service runs under — install it into that interpreter.
- **Cerbo MQTT broker (port 1883) unreachable** — the publisher can't subscribe.
- **Check the error log:** `/tmp/bsf-solar-dispatch.err`.

### Dashboard shows "reconnecting" / no live data

The page loads but never gets live numbers.

- **Relay not running** — the relay that bridges MQTT to the browser isn't up.
- **`ws://<cerbo>:9001` blocked / not listening** — the dashboard reads MQTT over WebSocket on
  **9001**. Confirm the **mosquitto WebSocket listener on port 9001** is enabled and reachable.

### adb can't see the phone (Android widget sideload)

- **Wireless-debug listener sleeps on screen lock** — Android stops accepting the connection when
  the screen locks, even though mDNS still advertises. **Wake the phone** (and keep the screen on)
  before connecting, then retry the `adb` command.

### tinytuya wizard returns no devices

- **Region mismatch** — same root cause as Tuya "All Devices empty." The wizard's **Region** must
  match your project's Data Center. Fix the region and re-run. See `region-datacenter-map.md`.

### iPhone widget (Scriptable)

The iOS home-screen widget runs in the free **Scriptable** app. Common issues:

- **"No data" in the widget** — the widget can't reach the relay. Either the **`STATE_URL`** in the
  script is wrong, or the relay's **`/solar/state`** isn't reachable from the phone. The phone must
  be on the **same LAN** as the relay, or reach it via the **public tunnel** if you've configured
  one. Check the URL resolves in Safari first.
- **It only updates every ~10 minutes** — that's iOS, not a bug. The system decides how often it
  wakes widgets to refresh; roughly every 10 minutes is normal. Open the dashboard in the browser
  for truly live numbers.
- **The script errors when you run it in-app** — check the **`STATE_URL`** has **no trailing space**
  and **starts with `https`**. A stray space or a missing scheme is the usual culprit.

Full setup is in **`widget/ios/README.md`**.

### Summer cooling (heating ↔ cooling)

Setting **`dispatcher.ac.mode`** to **`"cooling"`** in `config.json` flips the dashboard labels and
the setpoint sense — **but the Node-RED dispatcher ships with HEATING-direction temperature logic**
(it fires when the room is *cold*). The config flag does **not** auto-reverse the safety logic;
truly running the air-con as a cooler is a **deliberate manual step**:

1. Open the Node-RED editor (`https://<cerbo>:1881`) and open the **`ac_dispatcher_v15`** function
   node.
2. Flip the inside-temp comparisons. Heating fires when **`insideTemp < ON`** and stops at
   **`>= OFF`**; **cooling is the reverse** (fire when `insideTemp > ON`, stop at `<= OFF`).
3. Flip the **setpoint-ordering check** to match (heating expects `ON < OFF`; cooling is the
   reverse).
4. **Redeploy** the flow.

Test carefully after the change — you're editing the safety logic by hand. This reversal matches the
project's **v1.12 roadmap**; until then it isn't automatic. **If you're unsure, leave it in heating
mode.**

---

## How to read the logs

- **macOS** (where you run the install): the publisher/dispatcher write
  - **`/tmp/bsf-solar-dispatch.log`** — normal output
  - **`/tmp/bsf-solar-dispatch.err`** — errors (check this first)
- **Linux / launchd-style service:**
  - `journalctl --user -u farm.bsf.solar-dispatch-starter`

---

**Still stuck? Paste the exact error into Claude Code and it'll diagnose.** Include the log line
or error code verbatim — that's what makes the fix fast.
