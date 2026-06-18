# BSF Solar Dispatch Starter — Install Runbook (for Claude Code / Cowork to execute)

You are an AI coding assistant (Claude Code or Cowork). The user opened this folder and
wants to install **BSF Solar Dispatch** — a system that runs a Victron Cerbo GX's surplus
solar into discretionary loads (hot water, air-con, pumps) via Tuya/Smart-Life plugs, with
a phone dashboard + optional home-screen widgets. This file is your runbook. Work through
the steps **in order**, checking the success criteria before moving on.

---

## Prime directives (read before doing anything)

1. **You do the mechanical work. The user does the sign-ups.** You cannot create accounts
   or sign in for them (security). So the **Tuya developer account, Smart Life app, and any
   sign-in are USER ACTIONS** — you guide and wait. Everything else (config, flow deploy,
   service install, testing) is yours.
2. **Do NOT assume the user's system matches the author's.** This bundle's defaults describe
   one farm (Australian Victron Cerbo, ~5 kW solar, lead-acid bank, Tuya plugs). **Ask
   before defaulting.** A user with a different battery chemistry, array, or load set will
   get a broken or unsafe setup if you just accept the defaults. The wizard in Step 1 exists
   precisely to stop that.
3. **The dispatcher is a safety system. Never improvise its logic.** You are *configuring*
   a tested flow (turning its constants into the user's values), not redesigning it. Deploy
   it via `deploy.py` so the values are validated and the JS is `node --check`ed.
4. **The 30-day donation gate is dashboard-only and already built in.** Don't remove it or
   present it as something to configure. It never affects dispatch.
5. **If a step fails, stop and diagnose** with `install/troubleshooting.md`. Report the
   literal error. Verify before reporting done.

---

## What's in this bundle

```
config.example.json   the ONE system-config file — the wizard fills a copy (config.json)
node-red/             the dispatcher flow (ships with __TOKEN__ placeholders deploy.py fills)
dashboard/            the phone dashboard (single HTML) + generated dashboard-config.js
publisher/            the Python relay that feeds the dashboard + requirements.txt
widget/android/       Android home-screen widget (.apk)
widget/ios/           iOS home-screen widget (Scriptable .js + README)
install/              deploy.py + tuya-walkthrough + region map + troubleshooting
meta/                 Gumroad / forum copy (ignore during install)
```

`install/deploy.py` is your power tool: `check · flow · dashboard · publisher · widget ·
smoke`. Run `python3 install/deploy.py --help` once.

---

## Step 0 — Pre-flight (your machine)

- `python3 --version` (need ≥3.8). `python3 -m pip install -r publisher/requirements.txt`.
- Note OS (`uname`): launchd (macOS) vs systemd (Linux) for the service.
- `which node` — if present, deploy.py syntax-checks the flow's functions before deploying.
- `which adb` — only needed for the optional Android widget.

**Success:** Python ≥3.8, `paho-mqtt` installed.

---

## Step 1 — The configuration wizard (the important part)

Copy the template — `cp config.example.json config.json` — then fill it in by **interviewing
the user**. Read `config.example.json`: every field has an inline `_comment` explaining it +
a default. Go category by category. For each, **state the default and ask whether it matches
their system** (use AskUserQuestion for the multiple-choice ones — chemistry, AC mode, phone
type). If their rig is close to the default, they'll wave most of it through; if not, the
questions that matter will catch it. **Never silently accept a default for a value that
would be unsafe or non-functional if wrong** (battery chemistry, SOC windows, safety caps,
load wattages).

Collect, in this order:

1. **Site & location** — `site_name`; `location.lat/lon` (derive from town/postcode if they
   don't know) + `timezone` (IANA, e.g. `Australia/Brisbane`).
2. **Hardware identity** — `hardware.cerbo_ip` (Cerbo → Settings → Network); `mqtt_port`
   (default 1883); `vrm_portal_id` (Cerbo → Settings → VRM online portal); `inverter.model`
   + `rating_kw`; `fronius.present` (+ ip/size if yes).
3. **Chargers** — `hardware.chargers[]`, ONE entry per solar tracker (any count). For each:
   `kind` (`solarcharger` MPPT, or `multi` for a Multi-RS PV tracker), `instance` (VE device
   instance), a short unique `key`, a `label`, optional `kw`. Ask how many MPPTs/trackers
   they have and their instance numbers (from VRM / Node-RED). If they don't know, leave the
   default 4-tracker layout and tell them the per-charger drill-down may not match until they
   set it.
4. **Battery** (AskUserQuestion) — `chemistry`: **lead-acid / lifepo4 / lithium-ion**. This
   drives the SOC bands and is a safety input. Auto-fill `soc_danger_pct`/`soc_warn_pct` from
   chemistry (lead-acid 60/75 — must NOT sit low; lithium 20/40) and offer override. Capture
   `capacity_kwh`/`capacity_ah`, `float_voltage`/`charge_voltage` if known (optional).
5. **Solar** — `solar.total_dc_kw` (sum of trackers); `curtailment_mode` (leave `auto`).
6. **Tuya cloud** — leave `tuya.access_id/secret/api_host` as placeholders for now; Step 2
   fills them. (Set `api_host` once you've picked the data center in Step 2.)
7. **Loads** — `loads[]`, ONE entry per Tuya plug (0–N). Ask which loads they're switching.
   For each: `role` (`hot_water`/`air_con`/`inside_temp`/`well_pump`, or a custom label for
   extra switched loads), `label`, `rated_w` (nameplate watts), `essential` (true = not
   discretionary), `metered` (show live watts). Leave `device_id`/`local_key`/`local_ip`
   blank for now — Steps 2–3 fill them. **Delete the example loads they don't have.**
8. **Dispatcher thresholds** — walk these with their defaults, explaining each from the
   `_comment`s and **sizing to their system**: `surplus_on_w`/`surplus_off_w` (scale to array
   size), `curtail_fronius_w`, `load_cap_w`/`safety_cap_w` (⚠ set to THEIR circuit/wiring
   limits — these are the most install-specific + safety-relevant), `hw_element_w` (match
   their element), the `sustain_*` anti-flap timers, and `soc_windows` (W1/W2/W3 on/off —
   chemistry-influenced; lead-acid keeps SOC high, lithium can cycle lower).
9. **AC mode** (AskUserQuestion) — `dispatcher.ac.mode`: **heating** (fires when cold — what
   ships) or **cooling**. If they pick cooling, tell them plainly: this flips the dashboard
   labels + setpoints, but the dispatcher's temperature logic ships heating-direction;
   reversing it for true cooling dispatch is a documented manual step (troubleshooting.md →
   "Summer cooling"). Capture `inside_temp_on_c`/`off_c` and `inside_temp_source`.
10. **Display** — `display.currency`/`currency_symbol`/`price_per_kwh` (0 hides cost).
11. **Dashboard** — `dashboard.gumroad_url` (the seller's, usually leave as-is);
    `trial_days` (leave 30).

Write everything into `config.json`. **Re-running the wizard later just edits config.json
and re-runs deploy — no reinstall.**

**Success criterion — validate before proceeding:** `python3 install/deploy.py check`
reports **0 blockers**. It range-checks the values and flags incompatible combos (lithium
chemistry with a lead-acid SOC band, `surplus_on ≤ surplus_off`, `safety_cap < load_cap`,
non-monotonic SOC windows, heating setpoints with `on ≥ off`). Fix any blocker with the user.

---

## Step 2 — Tuya cloud setup (interactive, browser-driven)

The make-or-break step. Follow `install/tuya-walkthrough.md` + `install/region-datacenter-map.md`.
Drive the user's browser with **Chrome MCP** (`mcp__claude-in-chrome__*`) if available — read
each page back and narrate the clicks. Stop and let the **user** do sign-ups/sign-ins.

1. **Pick the data center** for their country (`region-datacenter-map.md`); set
   `tuya.api_host` in config.json to that endpoint. *(Australia is ambiguous — default
   Central Europe `https://openapi.tuyaeu.com`, fall back to Eastern America if linking
   returns no devices.)*
2. **User:** create / sign in to a Tuya developer account at `https://iot.tuya.com/`.
3. **Create a Cloud Project** (Development Method = Smart Home, Data Center = step 1).
4. **Subscribe the API service packs** (IoT Core, Authorization Token Management, Smart Home
   Basic Service, Device Status Notification); extend the trial if offered.
5. **Link the Smart Life app account** (Devices → Link App Account → scan the QR with the
   app). **User** scans.
6. **Confirm devices appear** under Devices → All Devices. **Empty → wrong data center** →
   change + re-link (the #1 failure).
7. **Copy the Access ID + Access Secret** (project Overview → Authorization Key) into
   `tuya.access_id` / `tuya.access_secret` in config.json.
8. **Read each device's ID** (Devices → All Devices) into the matching `loads[].device_id`.

**Success:** `tuya.access_id/secret/api_host` set; every plug in `loads[]` has its `device_id`.

---

## Step 3 — Per-device local keys (tinytuya)

The flow controls plugs on the LAN, which needs each plug's **local key**.
- `python3 -m pip install tinytuya` then `python3 -m tinytuya wizard` (with the user; it asks
  for the Access ID/Secret/region + one device ID). It writes `devices.json`.
- For each plug, copy from `devices.json` into `loads[]`: `key` → `local_key`, `ip` →
  `local_ip`. Full detail + cloud-UI fallback in `tuya-walkthrough.md` §8.

**Success:** each switched plug in `loads[]` has `local_key` + `local_ip`.
**If the user skips LAN control:** fine — leave those blank; the cloud-poll metering + gates
still work, only the flow's direct LAN-control branch stays idle.

---

## Step 4 — Deploy the dispatcher flow to the Cerbo

- Dry run: `python3 install/deploy.py flow --dry-run`. It stamps every config value into the
  flow, validates the JSON, and (if `node` is installed) `node --check`s every function body.
  **If it reports broken function code, STOP** — do not deploy; investigate.
- Deploy: `python3 install/deploy.py flow --deploy` → GETs the rev, POSTs the substituted
  flow (`type=flows`, only changed tabs restart). Output shows `Deployed. rev X → Y`.

**Failure:** connection refused → Cerbo offline. 401/auth → Node-RED admin auth is on; have
the user open `https://<cerbo>:1881`, then either disable auth or Import the generated
`.build/bsf-solar-dispatch.deployed.flow.json` manually in the editor. Paste errors into chat.

---

## Step 5 — Install the publisher service + configure the dashboard

- `python3 install/deploy.py publisher` (optionally `--target <dir>`). Installs the relay +
  a resolved config + the dashboard into the target and loads a launchd/systemd service
  (`farm.bsf.solar-dispatch-starter`).
- `python3 install/deploy.py dashboard` — writes `dashboard-config.js` (Cerbo IP, lat/lon,
  battery SOC bands from chemistry, currency, site name, AC mode, trial length, Gumroad URL).
- Serve the dashboard from the relay's data dir, e.g.
  `cd <data-dir> && python3 -m http.server 8780`.

**Success:** service loaded; `state.json` appears within ~30 s; the dashboard loads with live
numbers (or a clear "reconnecting" notice if the broker's down).
**Beta testers:** set `BETA_TESTER=1` before the `dashboard` step → 90-day trial. Re-arm with
`python3 install/deploy.py --reset-trial`, or open the dashboard with `?reset_trial=true`.

---

## Step 6 — Phones (ask which, then branch)

Ask (AskUserQuestion): **"Which phone(s) — Android, iPhone, or both?"** Then, per phone:

**Both / either — the zero-install option first (30 seconds):** the dashboard is a PWA. Tell
the user to open the dashboard URL on the phone and **Add to Home Screen** (iPhone: Safari →
Share → Add to Home Screen; Android: Chrome → ⋮ → Add to Home screen). It becomes a
full-screen app icon. Recommend this to everyone; it needs no third-party app.

**Android — optional native widget:** if they want the home-screen widget and have `adb`:
`python3 install/deploy.py widget` (wake/unlock the phone first — wireless-debug sleeps on
lock). Otherwise hand them `widget/android/BSF-Solar-Dispatch-v3.apk` to install manually.

**iPhone — optional native widget (Scriptable, ~5 min):** follow `widget/ios/README.md`.
Summary: the user installs the free **Scriptable** app, creates a new script, pastes
`widget/ios/scriptable-widget.js`, edits the `STATE_URL` at the top to their dashboard's
`/solar/state` URL (and `SOC_RED_PCT` if not lead-acid), runs it once to confirm data, then
long-presses the home screen → add a Scriptable widget → picks the script. (You can't sideload
to iOS — Scriptable is the sanctioned route; there's no native app by design.)

**Success:** the user can see the dashboard on their phone(s); any widget they wanted is placed.

---

## Step 7 — Smoke test + hand-off

- `python3 install/deploy.py smoke` — confirms `state.json` is fresh + `ok:true`.
- Open the dashboard yourself; read back what's on it. **Ask the user to confirm** the live
  numbers match reality (battery %, hot-water on/off, solar W).

**Success:** fresh state, plausible live data, user confirms. Tell them plainly: installed and
verified, here's the dashboard URL, here's how the trial works. Point them at `README.md` and
`install/troubleshooting.md`.

---

## If something goes wrong

Open `install/troubleshooting.md`, match the symptom, apply the fix. Tuya error codes
(1004/1106/…) map to causes there. If stuck, give the user the exact step + literal error —
enough for a fresh Claude Code session to pick it up.
