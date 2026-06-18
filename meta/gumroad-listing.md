# BSF Solar Dispatch Starter — Make your Victron Cerbo use your surplus ☀️

If your battery hits 100% by mid-morning and the panels spend the rest of the day
throttled back, you're throwing away free power. This is the system I built to fix that
on my own farm: it watches your Victron Cerbo GX, and when there's genuine surplus solar
it switches Tuya/Smart-Life smart plugs on — hot-water element, air-con, pumps — to soak
up the power that would otherwise be curtailed. The moment the surplus is gone or the
battery needs it, it backs off.

It's not a thermostat with a solar sticker on it. It's a *surplus dispatcher* with a
real safety envelope — battery state-of-charge windows, a hard night-time lockout, load
caps, and presence-aware air-con — built and run on a real Queensland farm, not a demo.

---

## Who this is for

You've got a Victron system and some Tuya/Smart-Life plugs, and you can see your battery
sitting at 100% while the array is curtailed for hours. That surplus is yours, you paid
for the panels, and right now it's going nowhere. This turns those idle hours into hot
water, a cooler (or warmer) house, and a full water tank — automatically, off solar
you'd otherwise waste.

If you don't have a Victron Cerbo and Tuya plugs, this isn't for you yet — see the
hardware list below.

---

## Hardware you need

- **Victron Cerbo GX** (or any Venus OS device) running **Node-RED** — the standard
  Victron Large image, or Node-RED installed on Venus OS.
- **Tuya / Smart Life smart plugs** for the loads you want to switch (hot-water element,
  air-con, pump…), plus a **Smart Life or Tuya Smart app account** with those plugs
  already paired.
- **A computer on the same LAN as the Cerbo** to run the dashboard relay — macOS or
  Linux, Python 3.8+. A Raspberry Pi is ideal (cheap, always on).
- **Any phone** for the dashboard — it's a web page, so iPhone or Android both work, and
  "Add to Home Screen" turns it into a full-screen app. **Optional** native-feel
  home-screen widgets too: an Android APK widget, and an iOS widget via the free
  **Scriptable** app (the script ships in the bundle). So a partner's iPhone is covered.
- **Claude Code or Cowork** to run the install — it does the fiddly parts for you.

---

## What's in the bundle

- **The dispatcher flow** (`node-red/bsf-solar-dispatch.flow.json`) — the brain. Imports
  straight into your Cerbo's Node-RED. Hot-water state machine, canonical Victron
  curtailment detection, air-con thresholds, presence gate, night lockout, load caps.
  Deploy it as shipped; it's a tested safety system, not a starting point.
- **The phone dashboard** (`dashboard/solar_dispatch_dashboard.html`) — a single HTML
  file you open on any phone (iPhone or Android); "Add to Home Screen" makes it a
  full-screen app. Live energy-flow diagram, SOC ring, per-charger solar breakdown, a
  sun/moon horizon arc, and weekly stats. On the LAN it also lets you tune air-con
  setpoints and presence.
- **The relay/publisher** (`publisher/solar_state_publisher.py`) — a small read-only
  Python service that reads the Cerbo and feeds the dashboard. Stdlib plus `paho-mqtt`,
  nothing heavy.
- **The home-screen widgets** (optional) — an **Android** APK (`widget/BSF-Solar-Dispatch-v3.apk`)
  and an **iOS** widget you run in the free **Scriptable** app (`widget/ios/`). Either gives
  you an at-a-glance home-screen view: SOC clock-arc, hot-water status, day/night theme.
- **The installer + docs** (`install/`) — `deploy.py` does the mechanical install, and
  the docs include a full Tuya cloud-setup walkthrough, a region→data-center map, and
  troubleshooting for the common errors.
- **One config file** (`config.example.json`) — copy to `config.json`, fill in your
  values, and both the relay and the installer read from it.

---

## Installing it

The whole point of this bundle is that you don't have to wire it together by hand.

1. Download and unzip the bundle (your Gumroad receipt link, or the canonical release
   below).
2. Open the folder with **Claude Code** or **Cowork**.
3. Say: **"Install BSF Solar Dispatch."**

> **Canonical download:** the source of truth and every versioned ZIP live on GitHub:
> **https://github.com/banksiasprings/bsf-solar-dispatch-starter/releases/latest**
> Direct v1.0.0 link:
> `https://github.com/banksiasprings/bsf-solar-dispatch-starter/releases/download/v1.0.0/bsf-solar-dispatch-starter.zip`
> Gumroad can either host the ZIP for buyers or point at this release — same bundle either way.

Claude reads the included runbook and does the fiddly parts — editing config, deploying
the flow to your Cerbo, installing the relay as a background service, and smoke-testing
that live data is actually flowing. Roughly **15–30 minutes**, most of it the one-time
Tuya cloud setup.

**You** only do the sign-ups, because it can't sign in as you: creating a Tuya developer
account and scanning a QR code with the Smart Life app. I'll be straight with you — the
Tuya cloud setup is the fiddly part of this whole thing (it always is). That's exactly
the part the bundle exists to make painless: it walks you through each click, picks the
right data center for your country, and diagnoses the usual "no devices showing" trap so
you don't have to learn Tuya's portal from scratch.

There's a manual install path in the README too, if you'd rather drive it yourself.

---

## It adapts to YOUR system, not just mine

This isn't hard-coded to my farm. The installer is a guided wizard (run by Claude Code)
that asks about *your* gear and configures everything to match: your **battery chemistry**
(lead-acid or lithium → the correct SOC bands so it protects *your* bank), your **solar
array and chargers**, your **Tuya plugs** (any number of them), your **dispatch thresholds
and safety caps**, and your **location** (for the sunrise/sunset arc). It ships with
sensible defaults for a typical Aussie **Victron + lead-acid + ~5 kW** setup, so a similar
rig breezes through; a different rig just answers the questions that matter. Nothing is
baked in to one property.

---

## What it costs

This is donate-ware. Run it **free for 30 days**. After that, if it's earning its keep,
chuck a couple of dollars in the tin.

- **Minimum $2 (AUD).** Pay what you want above that.
- Suggested: **$5 / $10 / $15.**
- **One-off. No subscription.** Pay once, it's yours forever, on your own systems.
- The donation reminder is on the **dashboard only**. Your hot water and air-con keep
  running whether the reminder is showing or not — the dispatcher never stops.
- Enter the **licence key** (a UUID) from your Gumroad receipt to dismiss the reminder
  permanently.
- Trust-based. **No server, no account, no phone-home** — the trial lives in your own
  browser.

I built this to recover wasted solar, not to run a billing department. The reminder is a
polite nudge, nothing more.

---

## The honest fine print

- **No warranty.** This switches real electrical loads off a battery bank. It ships with
  conservative defaults (SOC windows, a night lockout, load caps) — understand the flow
  before you widen them. Running big resistive loads off a lead-acid bank at night will
  hurt the bank. If you're unsure, leave the defaults and watch it on the dashboard for a
  few days first.
- **Support is community-flavoured.** There's no SLA, no helpdesk, no promise of updates.
  If something misbehaves, paste the error into Claude Code — the included
  troubleshooting doc covers the common ones. That's genuinely the best support channel.
- **Not affiliated with Victron or Tuya.** It runs *on* their gear; it isn't endorsed by
  either, and it carries no safety certification.
- Full terms in `LICENSE.md`. Plain English, no surprises.

---

## About

I'm Steven — a sole-trader farmer on the Queensland Granite Belt (cattle, pigs, sheep,
plus a bit of earthmoving). I got sick of watching my battery sit full while the panels
were throttled, so I built this to put that surplus to work, with Claude as my developer.
It runs my farm every day. I'm sharing it for coffee money in case it saves someone else
the same wasted power. Feel free to edit any of this to suit your setup — it's yours once
you've got it.

*Built on the Granite Belt, Queensland. If it saves you some power, you know what to do. ☕*

---

<!-- ─────────────────────────────────────────────────────────────────────────
SUGGESTED GUMROAD SUMMARY / TAGLINE (one line):
Turn your Victron Cerbo into a surplus-solar dispatcher — soak up wasted power into
hot water, air-con and pumps via Tuya plugs, with a real safety envelope and a phone
dashboard. Donate-ware, $2+.

SUGGESTED TAGS:
victron, cerbo-gx, node-red, solar, tuya, smart-home, home-automation, off-grid
───────────────────────────────────────────────────────────────────────── -->
