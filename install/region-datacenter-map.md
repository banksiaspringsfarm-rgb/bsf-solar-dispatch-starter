# Tuya Data Center → Region Map

The **Data Center** is the field most people get wrong in the Tuya cloud setup
(`tuya-walkthrough.md` §3). Pick the wrong one and your devices won't show up under
**Devices → All Devices**, and nothing downstream works.

This doc helps you pick — and tells you how to confirm it for real, because for some countries
(looking at you, Australia) it's genuinely ambiguous and you may have to try two.

If you get stuck, **paste the exact error into Claude Code and it'll diagnose.**

---

## The three things that MUST match

1. The **Data Center** you choose when creating the cloud project (§3).
2. The **`tuya.api_host`** you put in `config.json` (must be that DC's endpoint host).
3. The **home Data Center of your Smart Life / Tuya Smart app account** (set when the account was
   created — you can't easily change it).

If all three line up, devices appear and everything works. If `api_host` points at the wrong DC,
you'll get sign/permission errors; if the project DC ≠ the app account's home DC, **All Devices**
comes up empty.

---

## Data centers and their OpenAPI endpoint hosts

| Data Center | OpenAPI endpoint host (`tuya.api_host`) |
|-------------|-----------------------------------------|
| China | `https://openapi.tuyacn.com` |
| Western America | `https://openapi.tuyaus.com` |
| Eastern America | `https://openapi-ueaz.tuyaus.com` |
| Central Europe | `https://openapi.tuyaeu.com` |
| Western Europe | `https://openapi-weaz.tuyaeu.com` |
| India | `https://openapi.tuyain.com` |
| Singapore ⚠️ | `https://openapi-sg.iotbing.com` |

> ⚠️ **Singapore host is unverified** — single source, and Singapore uses the newer `iotbing.com`
> domain rather than `tuya.com`. If you're on Singapore, treat this host with suspicion and
> confirm empirically (see "How to confirm" below) before trusting it.

---

## tinytuya region codes

When you run `python3 -m tinytuya wizard` (walkthrough §8) it asks for a short **Region** code.
Use the one matching your project's Data Center:

| tinytuya code | Data Center |
|---------------|-------------|
| `cn` | China |
| `us` | Western America |
| `us-e` | Eastern America |
| `eu` | Central Europe |
| `eu-w` | Western Europe |
| `in` | India |
| `sg` | Singapore ⚠️ (unverified) |

---

## Country → Data Center lookup

Use this as your **first guess**, then confirm empirically.

| Country | Data Center | tinytuya code | Notes |
|---------|-------------|---------------|-------|
| USA / Canada | Western America | `us` | |
| **Australia** | ⚠️ **AMBIGUOUS** | `eu` then `us-e` | See AU note below |
| New Zealand | Western America | `us` | |
| UK / Germany / France / most of EU | Central Europe | `eu` | older accounts |
| UK / Germany / France / most of EU | Western Europe | `eu-w` | accounts created **after 2025-11-25** |
| India | India | `in` | accounts created **after 2020-09-22** |
| China | China | `cn` | |
| **Anything not listed** | Western America | `us` | default fallback |

### ⚠️ Australia — read this

There is **no single right answer** for Australia. It depends on when your Smart Life account was
created:

- **Most existing Smart Life accounts → Central Europe** (`eu`, `https://openapi.tuyaeu.com`).
  Try this **first**.
- **Newer accounts may be on Eastern America** (`us-e`, `https://openapi-ueaz.tuyaus.com`).

**What to do:** create the project on **EU first**, link your app account, and check
**Devices → All Devices**. If it's **empty** (or you hit **error 1106**), change the project's
Data Center to **Eastern America (`us-e`)**, re-link, and check again.

---

## Why it's account-dependent (two people, same country, different DC)

Tuya doesn't assign your DC purely by country — it's pinned to **when your app account was
created**, and Tuya has changed the defaults over time:

- **2025-06-03** — Tuya **added the Singapore** data center.
- **2025-11-25** — Tuya **re-mapped default regions** (this is why newer EU accounts land on
  Western Europe `eu-w`, and why some AU accounts are on Eastern America `us-e`).

An account **keeps the Data Center it was created under**, even after these remaps. So two people
in the same country, with accounts created on different dates, can be on **different DCs**. That's
why a country lookup can only be a first guess.

---

## How to confirm (empirically — there's no clean in-app screen)

There is **no tidy "which DC am I on" screen** in the Smart Life app. You confirm by experiment:

1. Pick the **predicted DC** from the table above.
2. Create the cloud project on that DC and **link your app account** (walkthrough §5).
3. Open **Devices → All Devices**:
   - **Devices show up** → you picked the right DC. Set `tuya.api_host` to that DC's host and
     carry on.
   - **Empty** (or error 1106 / "no devices returned") → **wrong DC.** Change the project's Data
     Center, re-link, and check again. For Australia: try `eu`, then `us-e`.

> Tip: the tinytuya wizard's **Region** prompt is the same decision — if the wizard returns no
> devices, that's the same "wrong DC" symptom (see `troubleshooting.md`).

---

## The bottom line for `config.json`

`tuya.api_host` **must** be the endpoint host of the DC that:

- you created the cloud **project** in, **and**
- your Smart Life **app account** calls home.

Get those three aligned and the rest of the install is easy. If you can't, **paste the exact
error into Claude Code and it'll diagnose.**
