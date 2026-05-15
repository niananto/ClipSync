# ClipSync V3 — Setup

V3 = Android phone runs a relay; Mac + Windows + (optionally) Android Termux run clients. iPhone is bridged through the Mac via Apple Universal Clipboard, same as V2.

> **Have V2 installed already?** Uninstall it first. V3's `--install`
> command will refuse to run on top of V2 and print the commands to
> remove it. See [V2 uninstall](#v2-uninstall) below.

## 0. Decide the host address

You need a name or IP for the Android phone that all clients use as `--host`. Pick whichever applies:

- **Tailscale MagicDNS name** (recommended): `phone.tail-xxxx.ts.net` — works on LAN and over the internet, encrypted end-to-end.
- **Tailscale IPv4**: `100.x.y.z` — same guarantees, less friendly.
- **LAN IP** (no Tailscale): `192.168.1.x` — only works on the home network; set a DHCP reservation so it doesn't change.

Confirm on the phone:
```sh
tailscale status
tailscale ip -4
```

## 1. Android phone (server + own client)

Full guide: [android/README.md](android/README.md). One-line summary:

```sh
# In Termux on the phone
pkg install python termux-api
termux-setup-storage
git clone <repo-url> ~/clipsync

# Inside proot Debian (where Tailscale already runs)
proot-distro login debian
apt update && apt install -y python3 git
git clone <repo-url> /root/clipsync
exit

# Auto-start at boot
mkdir -p ~/.termux/boot
cp ~/clipsync/android/boot-server.sh ~/.termux/boot/
cp ~/clipsync/android/boot-client.sh ~/.termux/boot/
chmod +x ~/.termux/boot/*.sh
```

Open the Termux:Boot app once so Android lets it run on boot. Reboot.

## 2. Mac (client only)

```bash
pip3 install -r requirements_mac.txt
```

Optional (better notifications):
```bash
brew install terminal-notifier
```

Test manually:
```bash
python3 clipsync_tray_mac.py --host phone.tail-xxxx.ts.net
```

Look for the clipboard icon in the **menu bar**. Right-click for Pause / Resume / Open ClipSync Folder / Quit.

Auto-start at login:
```bash
python3 clipsync_tray_mac.py --host phone.tail-xxxx.ts.net --install
```

If V2 is still installed, this prints uninstall commands and exits — run them first, then rerun.

Stop without uninstalling:
```bash
launchctl stop com.clipsync.v3.client
```

Uninstall:
```bash
python3 clipsync_tray_mac.py --uninstall
```

Logs: `~/Library/Logs/ClipSync/clipsync_v3.log`
Config: `~/.clipsync.conf`

## 3. Windows (client only)

> Run as your **normal user**, NEVER as administrator. Admin tasks run
> in a non-interactive session — no tray, no clipboard.

```powershell
pip install -r requirements_win.txt
```

Optional (nicer toasts):
```powershell
Install-Module -Name BurntToast    # in admin PowerShell, one-time
```

Test manually:
```powershell
python clipsync_tray_win.py --host phone.tail-xxxx.ts.net
```

Tray icon appears in the **system tray** (bottom-right, may be under the `^` overflow arrow). Right-click for the menu.

Auto-start at login:
```powershell
python clipsync_tray_win.py --host phone.tail-xxxx.ts.net --install
```

If V2 is still installed, this prints uninstall commands and exits.

Stop without uninstalling:
```powershell
schtasks /end /tn ClipSyncV3
```

Uninstall:
```powershell
python clipsync_tray_win.py --uninstall
```

Logs: `%APPDATA%\ClipSync\clipsync_v3.log`
Config: `%APPDATA%\ClipSync\clipsync.conf`

## 4. iPhone

Nothing to install. Make sure the Mac is signed in to the same iCloud account as the iPhone, both are on the same Wi-Fi, Bluetooth is on, and Handoff is enabled in **System Settings → General → AirDrop & Handoff** (Mac) and **Settings → General → AirPlay & Handoff** (iPhone). Then anything the Mac copies via ClipSync is available to iPhone, and vice versa.

If the Mac is asleep or off, iPhone clipboard sync stops — Universal Clipboard requires an awake Mac.

## V2 uninstall

If you're upgrading from V2:

**Mac:**
```bash
launchctl unload ~/Library/LaunchAgents/com.clipsync.server.plist
rm ~/Library/LaunchAgents/com.clipsync.server.plist
```
or
```bash
python3 v2/clipsync_tray_mac.py --uninstall
```

**Windows:**
```powershell
schtasks /end /tn ClipSync
schtasks /delete /tn ClipSync /f
```
or
```powershell
python v2\clipsync_tray_win.py --uninstall
```

Then proceed with the V3 install steps above.

## Troubleshooting

Run the diagnostic:
```bash
python3 clipsync_debug.py    # Mac
python clipsync_debug.py     # Windows
```

It checks: Python version, required packages, admin status (Windows), config file, V2 collision, V3 install state, and TCP reachability to the server.

Common issues:

- **`cannot reach <host>:9999`** — phone asleep, Tailscale down, server not running, or Tailscale ACL blocks. Check on the phone: `tail -f ~/.clipsync-server-boot.log`.
- **Tray icon missing on Windows** — check the `^` overflow area; drag the icon out to pin it. Also re-run `clipsync_debug.py` to confirm you're not running as admin.
- **Tray icon missing on Mac** — `System Settings → Privacy & Security → Accessibility` and make sure Terminal (or Python) is listed.
- **V3 install refuses to run** — V2 is still registered; run the V2 uninstall commands printed by the installer.
- **Nothing syncs between two clients** — both must be connected to the same server. Watch the server log; on each new connect it prints `+ client N connected from … (total=K)`. If `total` doesn't increase when you start a client, that client can't reach the server.
