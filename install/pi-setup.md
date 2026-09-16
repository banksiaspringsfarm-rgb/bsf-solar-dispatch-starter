# Raspberry Pi as the controller

For a **Selectronic** install the Pi *is* the controller: Node-RED runs the dispatcher, Mosquitto
is the broker, the Select.live bridge and the dashboard relay run as services, and Tailscale
lets you (or whoever installed it) reach it remotely after it's deployed at the site.
For a **Victron** install the same Pi just hosts the relay + dashboard (the flow stays on the
Cerbo) — the steps below still apply, skip nothing.

Tested on a **Raspberry Pi 5 (1 GB)** with Raspberry Pi OS Lite 64-bit (Trixie, 2026-09-15).
A Pi 4 works the same. **Use an SD card or NVMe, not a USB stick.** A USB 2.0 stick was tried
first: writes at ~140 kB/s, the Node-RED install took 40 min, the Pi then rebooted itself and
every file written in the previous minutes came back zero-length (repo, Tailscale state,
Node-RED's node_modules). Flashing an SD card took the whole build from hours to minutes.

## 1. Flash the image with the first-boot config baked in

Files in `install/pi/`: `user-data.example`, `network-config`, `meta-data.example`. They are
cloud-init; Raspberry Pi OS reads them from the boot partition on first boot and sets the
hostname, a user with your SSH key, then installs Mosquitto, Python, git, Tailscale, Node.js 22 +
Node-RED (+ the Tuya palette node, service enabled), clones this repo and builds its Python
venv. Step 4 below is therefore already done when `~/FIRSTBOOT_DONE` appears — it's kept for
repairing a box by hand.

1. Download Raspberry Pi OS Lite (64-bit) `.img.xz` from raspberrypi.com and verify the sha256.
2. Copy `user-data.example` → `user-data`, `meta-data.example` → `meta-data` and fill in:
   - `__HOSTNAME__` (e.g. `solar-pi-aunty`) in both files
   - `__YOUR_SSH_PUBLIC_KEY__` — the contents of your `~/.ssh/id_ed25519.pub`
   - `__SHA512_CRYPT_HASH__` — a password hash for the `bsf` user:
     `python3 -c "import crypt;print(crypt.crypt('YOUR-PASSWORD', crypt.mksalt(crypt.METHOD_SHA512)))"`
     (`openssl passwd -6` also works). Keep the plain password somewhere safe: it's the
     keyboard fallback if SSH ever breaks.
3. Write the image. Raspberry Pi Imager's GUI works (Choose OS → Use custom, then skip its
   own customisation), or on macOS:
   ```bash
   diskutil unmountDisk force /dev/diskN && xz -dc image.img.xz | sudo dd of=/dev/rdiskN bs=4m
   ```
   Then mount the `bootfs` partition and copy `user-data`, `network-config`, `meta-data`
   onto it. Eject.
4. No SD card in the slot if booting from USB. Ethernet in. Power on. First boot takes
   **5 min on SD, 20–40 min on a slow USB stick** — wait for `~/FIRSTBOOT_DONE` to appear.

## 2. Confirm it's up

```bash
ping -c1 <hostname>.local
ssh bsf@<hostname>.local 'cloud-init status; systemctl is-active mosquitto; which tailscale'
```
Expect `status: done`, `active`, `/usr/bin/tailscale`.

## 3. Tailscale (one tap by the account owner)

```bash
ssh bsf@<hostname>.local 'sudo tailscale up --hostname <hostname>'
```
It prints a `https://login.tailscale.com/a/…` link. Whoever owns the tailnet opens it on their
phone and taps Connect. From then on the Pi is reachable at `<hostname>` from any device on the
tailnet, wherever it's plugged in. Gotcha: running `tailscale up` inside a script that then
`pkill`s "tailscale up" kills the SSH session too (the pattern matches its own command line).

## 4. Node-RED

```bash
ssh bsf@<hostname>.local
curl -fsSL https://raw.githubusercontent.com/node-red/linux-installers/master/deb/update-nodejs-and-nodered -o ~/nr.sh
bash ~/nr.sh --confirm-install --confirm-pi --skip-pi --no-init --node22
sudo systemctl enable --now nodered
```
Editor at `http://<hostname>.local:1880`. Leave admin auth off (or deploy.py can't POST the
flow) — Tailscale is the access control.

The dispatcher's plug control uses `node-red-contrib-tuya-smart-device`; install it from
Manage palette or `cd ~/.node-red && npm i node-red-contrib-tuya-smart-device`.

## 5. Deploy the dispatcher onto it

Clone this repo **on the Pi** (`git clone …; cd bsf-solar-dispatch-starter`), then the normal
runbook: `config.json` with `hardware.inverter.kind = "selectronic"` (or `victron`),
`python3 install/deploy.py check`, `flow --deploy`, `publisher`, `dashboard`, `smoke`.
On a Selectronic install `publisher` also installs the Select.live bridge service.

`deploy.py publisher` on Linux also writes a `bsf-dashboard-http` user unit (static server on
:8780) and enables lingering so the user services run with nobody logged in. On the tailnet the
dashboard is `http://<hostname>:8780/solar_dispatch_dashboard.html`.

## 6. Deploying at the remote site

Everything is on the stick/card. At the site: plug in Ethernet + power. Because Tailscale is
already joined, `ssh bsf@<hostname>` works from home the moment it has internet. Then edit
`config.json` for the real Select.live IP + plug IDs and re-run `deploy.py` — no reflash.

If the site only has Wi-Fi, add a `wifis:` block to `network-config` before flashing (see
cloud-init netplan docs) or set it up on the LAN before it leaves.
