# Tuya Cloud Setup — Step-by-Step Walkthrough

This is the hardest part of the whole install. Don't worry — it's fiddly, not difficult.
Follow it in order and you'll come out the other side with everything `config.json` needs.

If you get stuck on any step, **paste the exact error into Claude Code and it'll diagnose.**

---

## §1 Overview — what you'll end up with

Tuya runs the smart plugs (hot water, air-con, well pump, the inside-temperature sensor).
To let the Cerbo control them, we need two kinds of access:

1. **Cloud access** — so we can read device status and switch things on/off from anywhere.
2. **Local keys** — a per-device secret that lets the Cerbo talk to each plug directly over your
   home network (faster, works even if the internet drops).

By the end of this doc you'll have collected all of this and dropped it into `config.json`:

| What | Goes into `config.json` at | Looks like |
|------|----------------------------|------------|
| Access ID (Client ID) | `tuya.access_id` | a long letters+numbers string |
| Access Secret (Client Secret) | `tuya.access_secret` | another long string — keep it private |
| Data-center API host | `tuya.api_host` | e.g. `https://openapi.tuyaeu.com` |
| Each plug → one entry in `loads[]` | `loads` (an **array**, one object per plug) | see below |
| Device ID (per plug) | `loads[].device_id` | `bf...` 20-ish chars |
| Local key (per plug) | `loads[].local_key` | 16 chars |
| Local IP (per plug) | `loads[].local_ip` | e.g. `192.168.1.50` |

Each plug is **one object in the top-level `loads` array**, keyed by its `role`
(`hot_water`, `air_con`, `inside_temp`, `well_pump`). So the per-device IDs, local keys,
and IPs all live together inside that plug's `loads[]` entry — not in separate
`tuya.devices` / `tuya.local_keys` / `tuya.local_ips` blocks. A single entry looks like:

```json
{ "role": "hot_water", "label": "Hot Water",
  "device_id": "bf...", "local_key": "0123456789abcdef", "local_ip": "192.168.1.50",
  "rated_w": 1600, "essential": false, "metered": true }
```

(The cloud creds — `tuya.access_id`, `tuya.access_secret`, `tuya.api_host` — stay where
they are. Only the per-plug device/key/IP data moved into `loads[]`.)

> **The single most common mistake** is the **Data Center** (§3). If you pick the wrong one,
> your devices won't show up and nothing else will work. There's a whole companion doc for it:
> **`region-datacenter-map.md`** — read it before §3.

---

## §2 Create a Tuya developer account

**(YOU do this — Claude can't sign in for you.)**

1. Go to **https://iot.tuya.com/**
2. Sign up for a developer account (email + password is fine).

> ⚠️ **This is a DIFFERENT account from the Smart Life / Tuya Smart phone app.**
> The phone app is where your plugs live. The developer site (iot.tuya.com) is the cloud
> control panel. They start out completely separate — in §5 we link them together.
> Don't try to log into iot.tuya.com with your phone-app login; it won't work.

![Tuya IoT developer sign-up page](screenshots/01-developer-signup.png)

When you're signed in and looking at the Tuya IoT Platform dashboard, tell Claude and we'll
move on.

---

## §3 Create a Cloud Project

**(YOU click through this — Claude will guide you via the browser and wait.)**

1. In the left menu go to **Cloud → Development**.
2. Click **Create Cloud Project**.

![Cloud → Development with Create Cloud Project button](screenshots/02-cloud-development.png)

3. Fill in the dialog:
   - **Project Name** — anything, e.g. `bsf-solar-dispatch`.
   - **Description** — optional.
   - **Industry** — anything (pick "Smart Home" or whatever's closest; it doesn't matter).
   - **Development Method** — **Smart Home**. (This one matters — pick Smart Home.)
   - **Data Center** — ⚠️ **the one from `region-datacenter-map.md`.**
     This is the field people get wrong. If you're in Australia, read the AU note in that
     doc carefully — it's ambiguous and you may have to try two.

![Create Cloud Project dialog](screenshots/03-create-project.png)

4. Create the project.

> If you're not 100% sure which Data Center, don't stress — §5 tells you how to confirm it
> empirically. If your devices don't show up, you came back here, change the Data Center, and
> re-link. That's normal.

---

## §4 Subscribe the API service packs

A new project doesn't automatically have permission to read device status or switch devices.
You subscribe to "API service packs" to grant those permissions.

In the project, go to **Cloud → Cloud Services** (or the **Service API** tab inside the project)
and **subscribe** to these packs:

| Pack name | Why |
|-----------|-----|
| **IoT Core** | base device access — required |
| **Authorization Token Management** | lets us get an auth token — required |
| **Smart Home Basic Service** | device list + control — required |
| **Device Status Notification** | live status updates — needed for status reads |

**Minimum to read status and switch devices:**
IoT Core + Authorization Token Management + Smart Home Basic Service.
Add **Device Status Notification** so status reads work properly.

![Subscribing to API service packs](screenshots/04-api-service-packs.png)

After subscribing, go back into the **project** and make sure each pack is **authorized for this
project** (there's usually an "Authorize" / link-to-project step — some pack pages have a
checkbox per project).

### Free trial — extend it now, proactively

New projects get the packs on a **free trial** (length varies — Tuya has changed it; don't rely
on a specific number of days). The trial can lapse silently and break everything with a
confusing error.

**Extend it now, before you have a problem:**

> **Cloud → Cloud Services → IoT Core → My Subscriptions → Extend Trial Period**

![Extend Trial Period under My Subscriptions](screenshots/05-extend-trial.png)

If you ever see **error `28841002`**, that's the trial having lapsed — come back here and
**Extend Trial Period** again. (See `troubleshooting.md`.)

---

## §5 Link the Smart Life / Tuya Smart app account

This is where the cloud project meets your actual phone-app devices.

**(YOU scan the QR with your phone — Claude can't do this part.)**

1. In the project go to **Devices** → **Link App Account** → **Add App Account**.
2. A **QR code** appears on screen.

![Link App Account — QR code](screenshots/06-link-app-qr.png)

3. On your phone, open the **Smart Life** (or **Tuya Smart**) app:
   - Tap the **Me** tab (bottom-right).
   - Tap the **scan icon** (top-right corner).
   - Scan the QR code on your computer screen.
   - Tap **Confirm**.

![Smart Life app — Me tab, scan icon](screenshots/07-app-scan.png)

4. Back on iot.tuya.com, go to **Devices → All Devices**.

**Confirm your devices appear here.** You should see your hot-water plug, air-con plug,
inside-temp sensor, well-pump plug, etc.

![Devices → All Devices showing the linked plugs](screenshots/08-all-devices.png)

> ⚠️ **#1 failure: "All Devices" is EMPTY.**
> If you linked the account but no devices show up, it's almost always the **wrong Data Center**.
> Go back to §3, **change the project's Data Center**, and **re-link the app account** here.
> See `region-datacenter-map.md` (especially the Australia note) — there's no clean "which DC am
> I on" screen, so this trial-and-error is the normal way to find it.

---

## §6 Get the Access ID + Access Secret

These are the keys that authenticate the Cerbo to the Tuya cloud.

1. In the project, open the **Overview** tab.
2. Find the **Authorization Key** section:
   - **Access ID / Client ID** → goes into `config.json` → `tuya.access_id`
   - **Access Secret / Client Secret** → goes into `config.json` → `tuya.access_secret`
     (click to reveal — keep it private, it's a password)

![Project Overview — Authorization Key (Access ID / Access Secret)](screenshots/09-authorization-key.png)

3. Set **`tuya.api_host`** to the **data-center endpoint host** for the DC you chose in §3.
   For example, Central Europe is `https://openapi.tuyaeu.com`. The full list is in
   `region-datacenter-map.md`. **It must match the DC the project was created in.**

> Side note: your account **UID** (sometimes needed) is under **Devices → the linked account**.
> The starter doesn't need the UID for normal operation, but it's there if asked.

So far `config.json` looks like:

```json
"tuya": {
  "access_id": "xxxxxxxxxxxxxxxxxxxx",
  "access_secret": "yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy",
  "api_host": "https://openapi.tuyaeu.com",
  ...
}
```

---

## §7 Get each device's ID

1. Go to **Devices → All Devices**.
2. Each device row shows its **Device ID** (a `bf...` string). Click a device to see it clearly /
   copy it.

![All Devices — each row shows its Device ID](screenshots/10-device-ids.png)

3. Map each device, by the name you gave it in the phone app, to its `role`, and put the
   Device ID into that plug's `device_id` field in `loads[]`:

| Role (`loads[].role`) | Which physical device | Field |
|-----------------------|-----------------------|-------|
| `hot_water` | the hot-water plug | `loads[].device_id` |
| `air_con` | the air-con plug | `loads[].device_id` |
| `inside_temp` | the inside temperature sensor | `loads[].device_id` |

> The **well pump** doesn't need a cloud device ID for the dispatcher's logic — it's controlled
> locally — so its `loads[]` entry can leave `device_id` empty (or omit it). But it **does** need
> a `local_key` and `local_ip` (§8).

```json
"loads": [
  { "role": "hot_water",   "label": "Hot Water",   "device_id": "bf1111111111111111aaaa", ... },
  { "role": "air_con",     "label": "Air-Con",     "device_id": "bf2222222222222222bbbb", ... },
  { "role": "inside_temp", "label": "Inside Temp", "device_id": "bf3333333333333333cccc", ... }
]
```

---

## §8 Per-device LOCAL KEYS — the painful part

Local keys let the Cerbo talk to each plug **directly over your LAN** — fast, and resilient if the
internet drops. Each device has its own 16-character key.

Tuya has been progressively **hiding** local keys in the cloud UI, so we lead with the reliable
tool: **tinytuya**.

### The reliable way — `tinytuya wizard`

Run these on any computer on the **same home network** as the plugs (your Mac is fine):

```bash
python3 -m pip install tinytuya
python3 -m tinytuya wizard
```

The wizard will ask you for:

| Prompt | What to enter |
|--------|---------------|
| **API Key** | your **Access ID** from §6 |
| **API Secret** | your **Access Secret** from §6 |
| **Region** | `cn` / `us` / `us-e` / `eu` / `eu-w` / `in` / `sg` — **must match the project Data Center** (see `region-datacenter-map.md`) |
| **Device ID** | any one Device ID from your account (§7) — or type **`scan`** to find one on the LAN |

It then queries the cloud and writes several files in the current folder:

- **`devices.json`** — the important one. For each device:
  `name`, `id`, **`key`** (← this is the local key), `ip`, and `version` (e.g. `3.1`–`3.5`).
- `snapshot.json`, `tuya-raw.json`, `tinytuya.json` — extra detail / raw dumps.

![tinytuya wizard prompts](screenshots/11-tinytuya-wizard.png)

**Map `devices.json` → the matching `loads[]` entry** (match them up by the device `name` in
`devices.json` → the plug's `role`):
- device's **`id`** → that load's **`device_id`**
- device's **`key`** → that load's **`local_key`**
- device's **`ip`** → that load's **`local_ip`**

…for `hot_water`, `air_con`, `inside_temp`, **and `well_pump`** — each one its own object in
the `loads` array:

```json
"loads": [
  { "role": "hot_water",   "label": "Hot Water",   "device_id": "bf1111111111111111aaaa",
    "local_key": "0123456789abcdef", "local_ip": "192.168.1.50", "rated_w": 1600,
    "essential": false, "metered": true },
  { "role": "air_con",     "label": "Air-Con",     "device_id": "bf2222222222222222bbbb",
    "local_key": "fedcba9876543210", "local_ip": "192.168.1.51", "rated_w": 900,
    "essential": false, "metered": true },
  { "role": "inside_temp", "label": "Inside Temp", "device_id": "bf3333333333333333cccc",
    "local_key": "a1b2c3d4e5f6a7b8", "local_ip": "192.168.1.52", "rated_w": 0,
    "essential": false, "metered": false },
  { "role": "well_pump",   "label": "Well Pump",   "device_id": "",
    "local_key": "1122334455667788", "local_ip": "192.168.1.53", "rated_w": 750,
    "essential": false, "metered": false }
]
```

> **`version`** matters for tinytuya/localtuya to talk to a plug (3.1–3.5). If a device later
> won't respond locally, the protocol version in `devices.json` is the first thing to check —
> note it down even though it doesn't have a slot in the `loads[]` entry.

### The older cloud-UI way (may not work)

There used to be a path in the cloud UI:

> **Devices → (pick a device) → Device Debugging**

…which showed the local key. **This may no longer be available** — Tuya has been hiding local
keys. If you can see it, great; if not, **use the tinytuya wizard above.**

![Device Debugging panel (may be unavailable)](screenshots/12-device-debugging.png)

### "I don't want to do cloud setup at all"

Be honest: you almost certainly still need the cloud link. `tinytuya scan` can **sometimes**
pick up local keys straight off the LAN, but in general **retrieving local keys requires the
cloud account link** (§5). So if `scan` comes up empty-handed, that's expected — go back and do
the cloud link, then run the wizard.

---

## You're done with Tuya

At this point `config.json`'s `tuya` block should have the cloud creds filled in — `access_id`,
`access_secret`, `api_host` — and the `loads` array should have one entry per plug, each with its
`device_id` (where applicable), `local_key`, and `local_ip`.

Hand back to Claude Code and let it run the next `python3 install/deploy.py` step.

**If anything errored along the way, paste the exact error into Claude Code and it'll diagnose** —
and check `troubleshooting.md` for the common Tuya error codes (1004, 1106, 2406, 28841002).
