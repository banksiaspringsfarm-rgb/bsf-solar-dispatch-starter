# Pricing & Licence Model — internal reference

*The single source of truth for what BSF Solar Dispatch costs and how the trial works.
Keep the Gumroad page, README, LICENSE.md and forum post consistent with this. All
amounts AUD.*

---

## The model in one line

Donate-ware. Free 30-day trial, then a dashboard-only "send me a coffee" reminder.
Minimum **$2**, pay-what-you-want above. One-off, no subscription, yours forever. The
dispatcher never stops, paid or not.

---

## Price

| | |
|---|---|
| **Minimum** | **$2 AUD** (Gumroad pay-what-you-want floor) |
| **Model** | Pay-what-you-want, one-off |
| **Subscription** | None — never |
| **Ownership** | Paid once = yours forever, on your own systems |

### Suggested tiers (shown on the Gumroad page as nudges)

| Tier | Amount | One-line rationale |
|---|---|---|
| Coffee | **$5** | "It works and I'm grateful" — the default thank-you. |
| Tank of hot water | **$10** | "It's already saved me more than this in wasted solar." |
| Backer | **$15** | "Keep building / I want to support the work." |

These are suggestions only. Anything from $2 up is fine; the floor is the only hard rule.

---

## Trial mechanics

- **30-day free trial**, counted from the first time the dashboard is opened. The clock
  lives in the user's **own browser** (localStorage) — there is no server.
- After 30 days a **donation card appears on the dashboard only**. It is a *reminder, not
  a lock*: the Node-RED dispatcher (hot water, air-con, pumps, the safety envelope) keeps
  running whether the card is showing or not.
- The user dismisses the reminder permanently by entering the **licence key** — a
  **UUID** printed on their Gumroad receipt.
- **Trust-based, no server, no account, no phone-home.** The key is validated locally;
  nothing is checked against a backend. The people who'd bother defeating it were never
  going to pay.
- Trial length is config-driven (`config.json` → `dashboard.trial_days`, default 30) and
  stamped into the dashboard at install by `deploy.py`.
- Test/reset hooks: `?test_paywall=true` compresses 30 days → 30 seconds for a quick
  check; `?reset_trial=true` (or `deploy.py --reset-trial`) re-arms the trial.

---

## BETA_TESTER — 90-day option

For the 2–3 friends I'll beta with before launch:

- Set the environment variable **`BETA_TESTER=1`** before running the `dashboard` install
  step (`deploy.py` reads it and stamps **`trial_days = 90`** instead of 30).
- Gives testers a comfortable 90-day window with no reminder, so feedback isn't muddied
  by a donation card popping up mid-test.
- It's just a longer trial — same trust-based, no-server mechanic. No separate key or SKU
  needed.

---

## Gumroad

- **URL (placeholder — confirm before launch):**
  `https://banksiaspringsfarm.gumroad.com/l/solar-dispatch`
- Configure the Gumroad product as **pay-what-you-want** with a **$2 minimum** and the
  suggested $5 / $10 / $15 amounts.
- The licence key delivered on the receipt is the **UUID** users paste into the dashboard
  to dismiss the reminder.
- This same URL is wired into `config.json` → `dashboard.gumroad_url` (the dashboard's
  Donate button points there).
