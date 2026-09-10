<!-- Draft post for the Victron Community forum (community.victronenergy.com).
     Tone: "I built this for my farm, here's what it does and what I learned."
     Not an ad. Steven can edit before posting. -->

**Suggested title:** Built a surplus-solar dispatcher on my Cerbo (Node-RED + Tuya plugs) — soaks up curtailed PV into hot water / air-con, with a phone dashboard

---

G'day all,

Like a lot of you, my battery was hitting 100% by mid-morning and then the array would
sit throttled for the rest of a good day. All that PV potential just going nowhere. I
run a small farm on the Queensland Granite Belt and I wanted that surplus going into hot
water, the house air-con, and the water pump instead of being curtailed away. So over a
few weekends (with an AI assistant doing the heavy typing) I built a dispatcher that runs
on the Cerbo, and it's been controlling those loads on my place every day since. Thought
I'd share what it does and a couple of things I learned, in case it's useful.

**What it does, high level**

- **Node-RED on the Cerbo GX** is the brain. It reads the system over the Cerbo's MQTT,
  and when there's genuine surplus it switches **Tuya/Smart-Life smart plugs** on
  (hot-water element, air-con, pump), then backs off when the surplus is gone or the
  battery wants it back.
- A small **Python relay** on a Pi on the same LAN reads the Cerbo and feeds a
  **single-file phone dashboard** — live energy-flow diagram, SOC ring, per-charger
  (MPPT) solar breakdown, a sun/moon horizon arc, weekly stats. It's a web page, so it
  works on **iPhone or Android** (add-to-home-screen makes it a full-screen PWA), with
  optional home-screen widgets for each (an Android APK, or an iOS widget via the free
  Scriptable app).
- It is **not** a dumb thermostat. It runs inside a safety envelope: battery
  state-of-charge windows, a **hard night-time lockout**, **load caps**, and
  **presence-aware air-con**. Conservative defaults out of the box.

(attach: `docs/images/dashboard-flow.png` and `docs/images/android-widget.png` from the repo)

**A few things I learned building it (the bits that mattered)**

1. **Detect *real* curtailment — don't guess from SOC.** My first cut fired loads off
   "battery is full-ish," which short-cycled and chased its own tail. The fix was to read
   the actual Victron throttle state (the chargers reporting they're in current-limited
   operation) rather than inferring surplus from SOC or a Fronius reading. Once it
   dispatched on genuine MPPT/charger curtailment, the short-cycling stopped. If you're
   rolling your own, that distinction is worth getting right early.

2. **Don't run big resistive loads off a lead-acid bank at night.** Mine's lead-acid, and
   the night-time lockout isn't a nicety — it's the whole reason the bank survives. A
   hard force-OFF window after dark is non-negotiable for me. Same goes for the load caps:
   the dispatcher won't stack loads beyond what the surplus can actually carry.

3. **Presence-aware air-con earns its keep.** No point cooling/heating an empty house off
   surplus. The air-con gate checks whether anyone's home before it heats on solar — but
   it never bypasses the SOC/solar safety envelope to do it. Comfort is gated under
   safety, not the other way round.

4. **The Tuya cloud setup is the fiddly bit** — picking the right data center for your
   region so your devices actually show up was the single most painful step for me. I've
   written that part up so others don't lose the afternoon I did.

**On cost / how I'm sharing it**

It's free for personal use, source on GitHub. The Node-RED threads on this forum got me most
of the way to my own install, so this is me putting something back. There's a "buy me a
coffee" link on the dashboard if it ends up earning its keep at your place — never
required, never nags more than once, and the dispatcher doesn't know it exists. No server,
no account, no phone-home.

It installs with Claude Code / Cowork doing the mechanical parts (config, deploying the
flow, the relay service, smoke test) — about 15–30 minutes, most of it that one-time Tuya
setup, which it walks you through.

Repo, install line and docs:
**https://github.com/banksiaspringsfarm-rgb/bsf-solar-dispatch-starter**

I'm not trying to spam the forum — just sharing something I built that scratched a real
itch, and the link's there if it scratches yours too.

Happy to answer questions about the curtailment detection, the Node-RED flow, or the
safety envelope — and very keen on feedback from anyone running a different battery
chemistry or inverter setup than mine. Cheers.

— Steven, Granite Belt QLD
