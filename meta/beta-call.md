# Beta-tester call — post copy + where to post

Status: DRAFT for Steven (2026-09-11). Nothing posted yet. The one-line installer needs the
public GitHub repo to exist first — post nothing until the `curl … | sh` line actually works.

---

## The post (short version — Reddit / Facebook / forum "share your project" threads)

**Title:** Looking for ~10 Victron owners to beta-test a surplus-solar dispatcher (Cerbo + Node-RED + smart plugs) — boats, vans, off-grid houses

G'day. I run a small off-grid farm in Queensland and built a dispatcher that runs on the
Cerbo GX: when the battery's full and the MPPTs are throttling, it switches smart plugs on
(hot water, air-con, pump) to soak up the power that'd otherwise be curtailed, then backs off
the moment the surplus is gone or the battery wants it. It's been running my place daily
since autumn. Phone dashboard, home-screen widgets, hard night-time lockout, SOC windows,
load caps.

I'm looking for about ten people with different rigs to mine to try it and tell me where it
breaks. Especially keen on:

- **lithium banks** (mine's lead-acid, so the defaults are conservative)
- **boats and motorhomes** — 12/24 V systems, small arrays, a diesel heater or a water maker
  as the load
- **off-grid houses** with a Fronius or other AC-coupled inverter

What you need: a Cerbo GX (or Venus OS) with Node-RED, one or more Tuya / Smart Life plugs, a
Mac, Linux box or Raspberry Pi on the same network, and a Claude subscription — the install
is driven by Claude Code, which reads photos of your gear, works out what will and won't work,
then does the config, flow deploy and service install with you. About 15–30 min, most of it
the one-time Tuya cloud sign-up.

Install is one line — it's at the top of the README:
https://github.com/banksiaspringsfarm-rgb/bsf-solar-dispatch-starter

Free for personal use, forever — source on GitHub, for everyone, not just beta testers. There's a "buy me a
coffee" link on the dashboard if you ever feel like it. No account, no server, no phone-home.

What I want back: did the install get through, did the photo step identify your gear right,
did it dispatch on a real surplus day, and what did it get wrong. Reply here or open an issue
on the repo. I'll fix things as they come in.

— Steven, Granite Belt QLD

---

## Where to post — ranked

Rules of thumb: post in "share your project / modifications" spaces, not general help; lead
with what it does and what you learned, not the link; answer every reply for the first week.
One post per community; don't cross-post the same day.

**Spam-filter lesson (Victron Community, 11 Sep):** a new account + external link + a
`curl … | sh` line = "Account temporarily on hold" and the post hidden until a moderator
clears it. Before posting anywhere new: reply helpfully in 2–3 existing threads first, put
ONE link (the repo) in the post, and never paste the curl line — say "one-line install in
the README". New accounts usually can't upload images either; add screenshots by editing
the post once trust level 1 kicks in.

| # | Community | Where exactly | Why / notes |
|---|---|---|---|
| 1 | **Victron Community** | https://community.victronenergy.com/c/node-red/28 (Node-RED) — or Modifications category | The Victron-owning audience, Node-RED section is exactly this. Use the long post in `forum-post.md`, add the beta ask at the end. Highest-value single post. |
| 2 | **DIY Solar Power Forum** (Will Prowse) | https://diysolarforum.com → Victron subforum, or "Show and tell / Off-Grid" | Biggest English-language DIY solar forum; lots of off-grid houses + van builds. There is an existing "any dedicated Victron/Node-RED threads?" thread — reply there too. |
| 3 | **r/VictronEnergy** | reddit.com/r/VictronEnergy | Small but exactly on target. |
| 4 | **r/SolarDIY** | reddit.com/r/SolarDIY | Large; project posts do well; read sidebar re self-promo — frame as beta call, free. |
| 5 | **r/OffGrid** + **r/OffGridCabins** | reddit | Off-grid houses. |
| 6 | **r/vandwellers**, **r/vanlife**, **r/GoRVing** | reddit | Vans/motorhomes. Victron is the default install in the van scene. Post to one first, see how it lands. |
| 7 | **Cruisers Forum** | https://www.cruisersforum.com → Electrical: Batteries, Generators & Solar | Boats. Very Victron-heavy, technical crowd, tolerant of project posts. |
| 8 | **Home Assistant Community** | https://community.home-assistant.io → Share your Projects | Not Victron-specific, but the HA crowd runs Node-RED and Tuya and asks the right questions. |
| 9 | **Node-RED Forum** | https://discourse.nodered.org → Share Your Projects | Developer feedback on the flow itself. |
| 10 | **Whirlpool — Green tech** | https://forums.whirlpool.net.au (Green tech) | Australian; off-grid + Selectronic + Victron owners in one place. Good for later Selectronic beta. |
| 11 | **Facebook groups** (Steven's account) | "Victron Energy Users Group", "Off Grid Solar Australia", "DIY Solar Australia", "Australian Caravan & Motorhome Solar & Batteries" | Highest AU reach, lowest signal. Post short version + dashboard screenshot. |
| 12 | **Expedition Portal** | https://expeditionportal.com/forum → Power Systems | Overland/4WD tourers, Victron-heavy. |

Skip for now: Hacker News / Product Hunt (wrong crowd until it's polished), Victron's official
Facebook page (they don't allow third-party promotion).

## Before posting — checklist

- [ ] Public GitHub repo exists and the `curl … | sh` line works from a clean Mac and a clean Pi
- [x] Real screenshots in `docs/images/` (cropped: no hostname, no personal photos)
- [ ] Ko-fi (or Buy Me a Coffee) page created by Steven and `tip_url` updated everywhere
- [ ] GitHub Issues enabled on the repo — that's where "it broke" reports go
- [ ] Steven has 30 min/day for a week to answer replies
