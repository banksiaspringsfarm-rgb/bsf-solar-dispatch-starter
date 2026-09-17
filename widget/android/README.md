# Android home-screen widget — being rebuilt

There is no Android app in the bundle right now.

**Why:** the `.apk` shipped from v1.0.0 until 2026-09-18 was not a solar-only app. It was the
author's own phone dashboard app with the solar widget inside it, built with an address and an
access key for the author's server. That was a packaging mistake. The key only ever opened two
read-only status pages, it has been replaced, and the file has been removed. If you downloaded
the earlier `.apk`, delete it — it never talked to *your* system anyway.

**What's coming:** a small standalone solar-only app, with the same widget, where you set your
own dashboard's address on the phone. No keys, nothing baked in.

**What to use today (30 seconds, works on any phone):** open your dashboard in Chrome, then
⋮ → **Add to Home screen**. It becomes a full-screen app icon. iPhone users also have the
Scriptable widget in [`../ios/`](../ios/).
