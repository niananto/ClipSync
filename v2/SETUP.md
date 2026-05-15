# ClipSync V2 — Setup Guide

All files should live in the same folder on each machine.

---

## File layout

```
ClipSync/
├── clipboard_server_v2.py   ← Mac only
├── clipsync_tray_mac.py     ← Mac only
├── clipboard_client_v2.py   ← Windows only
└── clipsync_tray_win.py     ← Windows only
```

---

## Mac setup

### 1. Install dependencies
```bash
pip3 install pystray Pillow pyobjc-framework-Cocoa
```

Optionally, for more reliable notifications:
```bash
brew install terminal-notifier
```

### 2. First run (manual, to verify it works)
```bash
python3 clipsync_tray_mac.py
```
A clipboard icon will appear in your **menu bar**. Click it to Pause/Resume/Quit.

### 3. Auto-start on login
```bash
python3 clipsync_tray_mac.py --install
```
ClipSync will now start automatically every time you log in.

To stop without uninstalling:
```bash
launchctl stop com.clipsync.server
```

To start again without rebooting:
```bash
launchctl start com.clipsync.server
```

To fully uninstall auto-start:
```bash
python3 clipsync_tray_mac.py --uninstall
```

Logs live at: `~/Library/Logs/ClipSync/clipsync.log`

---

## Windows setup

### 1. Install dependencies
```powershell
pip install pystray Pillow pyperclip pywin32
```

Optionally, for nicer toast notifications:
```powershell
# In PowerShell as admin:
Install-Module -Name BurntToast
```

### 2. Find your Mac's local IP
On your Mac: **System Settings → Network → Wi-Fi → Details**
It will look like `192.168.1.42`

### 3. First run (manual, to verify it works)
```powershell
python clipsync_tray_win.py --host 192.168.1.42
```
A clipboard icon will appear in the **system tray** (bottom-right, you may need
to click the ^ arrow to find it). Right-click it to Pause/Resume/Quit.

The host IP is saved automatically to `%APPDATA%\ClipSync\clipsync.conf`
so you won't need `--host` again.

### 4. Auto-start on login
```powershell
python clipsync_tray_win.py --host 192.168.1.42 --install
```

> ⚠️ **Do NOT run `--install` as Administrator.** Admin elevation causes Windows
> to run the task in a non-interactive background session — no tray icon appears
> and clipboard access doesn't work. Run it as your normal user account.
> If you already ran it as admin, uninstall and redo it:
> ```powershell
> python clipsync_tray_win.py --uninstall
> python clipsync_tray_win.py --install   # no admin this time
> ```

ClipSync will now start automatically every time you log in.

To stop the running instance:
```powershell
schtasks /end /tn ClipSync
```

To start it again without rebooting:
```powershell
schtasks /run /tn ClipSync
```

To fully uninstall auto-start:
```powershell
python clipsync_tray_win.py --uninstall
```

Logs live at: `%APPDATA%\ClipSync\clipsync.log`

---

## Tray menu reference

| Item | Behaviour |
|---|---|
| ClipSync — ● Active | Status indicator (not clickable) |
| ClipSync — ⏸ Paused | Status indicator when paused |
| Pause Sync | Stop syncing; connection stays alive |
| Resume Sync | Resume syncing (replaces Pause when paused) |
| Open ClipSync Folder | Opens ~/Downloads/ClipSync in Finder/Explorer |
| Quit | Stops sync and exits completely |

---

## Troubleshooting

**Tray icon doesn't appear (Mac)**
pystray on macOS requires the script to run on the main thread and have
Accessibility permissions. If the icon is missing, check:
System Settings → Privacy & Security → Accessibility → add Terminal (or Python)

**Tray icon doesn't appear (Windows)**
Check the system tray overflow area (^ arrow near the clock). You can drag
the ClipSync icon out to always show it.

**"Could not import clipboard_server_v2" error**
Make sure all four files are in the same folder.

**Mac IP changes**
If your Mac's local IP changes (uncommon on home networks), update it:
```powershell
python clipsync_tray_win.py --host 192.168.1.NEW_IP
```
This saves the new IP and you're done. Consider setting a DHCP reservation
in your router for your Mac's MAC address so the IP never changes.
