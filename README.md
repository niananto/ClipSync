# ClipSync

A lightweight, open clipboard sync between your Windows PC, Mac, and iPhone — no cloud account required, no third-party service, just a direct LAN connection.

```
Windows ──── <ClipSync> ──── Mac ──── <Handoff> ──── iPhone
```

The Mac acts as the hub. Windows syncs to the Mac over your local network, and the Mac's built-in Universal Clipboard (Handoff) takes care of the iPhone side automatically.

---

## Features

- **Text** — syncs instantly in both directions
- **Images** — clipboard images (screenshots, browser copies) sync directly; large images are saved to the ClipSync folder automatically
- **Files** — copy a file in Finder or Explorer and paste it on the other machine natively (Cmd+V / Ctrl+V works)
- **Tray icon** — Mac menu bar icon and Windows system tray icon with Pause / Resume / Quit
- **Auto-start** — starts silently at login on both platforms, no terminal window
- **Notifications** — toast notifications on both platforms when a file or large image arrives
- **Conflict resolution** — received files are saved as `photo.png`, `photo (2).png` etc. if a name clash exists

### Content size rules

| Type | Behaviour |
|---|---|
| Text | Always synced |
| Image < 10 MB | Synced directly to clipboard — paste anywhere |
| Image 10–50 MB | Saved to ClipSync folder + notification |
| Image > 50 MB | Skipped with a warning |
| File < 50 MB | Transferred, saved to ClipSync folder, native paste works |
| File > 50 MB | Skipped with a warning |

---

## File layout

Each machine only needs its own two files — they must be in the same folder.

```
Mac
├── clipboard_server_v2.py
└── clipsync_tray_mac.py

Windows
├── clipboard_client_v2.py
└── clipsync_tray_win.py
```

The `ClipSync/` folder inside your Downloads is created automatically on both sides and is where received files and large images land.

---

## Quick start

### Mac

```bash
pip3 install pystray Pillow pyobjc-framework-Cocoa
python3 clipsync_tray_mac.py
```

A clipboard icon appears in your **menu bar**. Click it to Pause / Resume / Quit.

### Windows

```powershell
pip install pystray Pillow pyperclip pywin32
python clipsync_tray_win.py --host 192.168.10.145
```

A clipboard icon appears in your **system tray** (bottom-right — check the ^ overflow if you don't see it immediately). Right-click for the menu.

> The Mac IP is saved after the first run so you won't need `--host` again.

For full setup instructions including auto-start on login, see [SETUP.md](SETUP.md).

---

## Tray menu

| Item | Behaviour |
|---|---|
| ClipSync — ● Active | Status indicator |
| ClipSync — ⏸ Paused | Status when paused |
| Pause Sync | Suspends sync; connection stays alive for instant resume |
| Resume Sync | Resumes sync |
| Open ClipSync Folder | Opens `~/Downloads/ClipSync` in Finder / Explorer |
| Quit | Stops ClipSync entirely |

---

## Roadmap

| Version | Status | What it covers |
|---|---|---|
| V1 | ✅ Done | Text only, LAN |
| V2 | ✅ Done | Images, files, tray icon, auto-start |
| V3 | 🔜 Planned | Tailscale (works outside your home network), TLS encryption, shared-secret authentication |

V3 will require almost no structural change — the codebase is already wired with `V3 TODO` markers at every extension point. The sync logic stays the same; only the transport layer changes.

---

## How it works

ClipSync polls the clipboard on both machines every 500ms and hashes the content to detect changes. When a change is detected it serialises the payload — text as UTF-8, images as PNG bytes, files as raw bytes — into a length-prefixed JSON frame and sends it over a plain TCP socket on port 9999.

The Mac runs the server (always listening). Windows runs the client (auto-reconnects if the connection drops). Echo loops are prevented by tracking the hash of the last sent or received payload, and received files are never re-synced because the ClipSync folder itself is filtered out of outgoing file copies.

---

## Troubleshooting

**Tray icon not visible (Windows)**
It's probably in the overflow area. Click the `^` arrow near the clock, find the ClipSync icon, and drag it onto the taskbar to pin it permanently. Or go to Settings → Personalization → Taskbar → Other system tray icons.

**Tray icon not visible (Mac)**
Check System Settings → Privacy & Security → Accessibility and make sure Terminal (or your Python app) is listed.

**No notifications (Mac)**
Either install `terminal-notifier` (`brew install terminal-notifier`) or grant notification permission to Terminal in System Settings → Notifications.

**No notifications (Windows)**
Install `plyer` (`pip install plyer`) for the most reliable toast notifications.

**Auto-start not working (Windows)**
Make sure you ran `--install` as your **normal user, not as Administrator**. Admin elevation causes the task to run in a non-interactive session with no tray or clipboard access. If in doubt, run `clipsync_debug.py` — it will tell you exactly what's wrong.

**Mac IP changed**
Update it on Windows:
```powershell
python clipsync_tray_win.py --host <NEW_IP>
```
This saves the new IP automatically. To avoid this happening again, set a DHCP reservation for your Mac in your router settings.

**Mac is asleep**
When the Mac is asleep, sync is paused — including the iPhone side, since Universal Clipboard requires an awake Mac. Wake-on-LAN support is being considered for V3.

**Run the diagnostics tool**
If something isn't working on Windows and you're not sure why:
```powershell
python clipsync_debug.py
```
It checks privileges, dependencies, config, Task Scheduler registration, and recent logs in one pass.

---

## Requirements

### Mac
- macOS 12 or later
- Python 3.10+
- `pystray`, `Pillow`, `pyobjc-framework-Cocoa`
- Optionally: `terminal-notifier` (via Homebrew)

### Windows
- Windows 10 or later
- Python 3.10+
- `pystray`, `Pillow`, `pyperclip`, `pywin32`
- Optionally: `plyer` or BurntToast PowerShell module (for notifications)

---

## Security note

V2 runs over plain TCP with no encryption or authentication. This is intentional for a home LAN — the attack surface is limited to devices already on your network. V3 will add TLS and a shared-secret HMAC handshake, making it safe to use over Tailscale across networks.

Do not expose port 9999 to the internet in the meantime.
