# iOS Home-Screen Widget (Scriptable)

A native iOS home-screen widget that mirrors the Android widget: battery **SOC**,
**PV power**, **Surplus**, and **Hot Water** + **Air-Con** state — refreshed from your
solar-dispatch relay.

## What is Scriptable?

[Scriptable](https://apps.apple.com/app/scriptable/id1405459188) is a **free** app on the
App Store that runs JavaScript and can render real iOS home-screen widgets. We can't ship a
native iOS app for a $2 product (Apple's dev fee + App Store review make that impractical),
so Scriptable is the right shape: you paste one small script, add a widget, and you're done.

## Two paths — which is this one?

- **Path 1 — true widget (this folder).** A real home-screen widget via Scriptable. Live
  SOC/PV tiles on your home screen, auto-refreshing. Needs the free Scriptable app installed.
- **Path 2 — "Add to Home Screen" (PWA).** The zero-install option covered in the main
  install guide: open the dashboard in Safari and add it to your home screen. It's a full-screen
  web app, not a widget — no install of anything, but it only updates while open.

If you want a glanceable tile that updates on its own, use this path.

## Install — step by step

1. **Install Scriptable** from the App Store (free).
2. **Open Scriptable**, tap the **+** (top-right) to create a new script. Name it **BSF Solar**
   (tap the script's title to rename it).
3. **Paste** the entire contents of `scriptable-widget.js` into the editor.
4. **Edit the config at the top of the script:**
   - `STATE_URL` — your relay's state endpoint, of the form
     `https://<your-relay-host>/solar/state`
   - `SOC_RED_PCT` — leave at `60` for lead-acid batteries. If you run **LiFePO4 / lithium**,
     lower it to about `20` (lithium can run much deeper before it's a problem).
   - `SITE_NAME` — optional header label.
5. **Run it once in-app** (the ▶ button, bottom-right). You should see a medium preview pull
   live data. If it shows "No data", fix the URL (see Troubleshooting) before adding the widget.
6. **Add it to your home screen:**
   - Long-press an empty area of the home screen → tap **+** (top-left) → search **Scriptable**.
   - Choose **Small** or **Medium** → **Add Widget**.
   - Long-press the placed widget → **Edit Widget**.
   - Set **Script** = **BSF Solar**.
   - *(Optional)* set **Parameter** to your state URL — this overrides `STATE_URL` for that one
     widget, so you can point different widgets at different relays without editing the code.
   - Tap away. Done.

**Small** shows SOC + compact PV/surplus + HW/AC dots. **Medium** shows all four metrics with
labels and ON/OFF chips. The theme follows your iOS light/dark appearance automatically.

## Refresh rate

The widget asks iOS to refresh about every **10 minutes**. iOS budgets widget refreshes to save
battery, so it may update less often — and only the OS decides exactly when. **This is an iOS
limitation, not a bug.** Open the script in-app any time for an instant, current reading.

## Troubleshooting

- **"No data" / "Check relay / URL"** — the widget couldn't fetch `/solar/state`. Usual causes:
  - The URL is wrong. It must end in `/solar/state` and be reachable **from the phone**.
  - The relay isn't running, or isn't reachable from where the phone is. If the relay is
    LAN-only, the phone must be on the **same Wi-Fi**; for remote access, use the **public
    tunnel** URL (if you've set one up) instead of a `192.168.x.x` LAN address.
  - Test the URL in the phone's browser first — you should see raw JSON.
- **Numbers show "–"** — that field came back `null` from the relay (normal during gaps); the
  widget keeps running.
- **Any Scriptable error** — copy the error text and paste it into Claude Code; we'll sort it.
