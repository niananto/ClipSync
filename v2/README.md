# ClipSync V2 — LAN with images, files, tray

Frozen feature-complete release. Mac = server. Windows = client. One-to-one only. Adds image + file sync, tray icons, auto-start.

## Changelog from V1

| Area | V1 | V2 |
|---|---|---|
| Content types | text | text, images (PNG bytes), files |
| Image size | n/a | <10MB clipboard, 10–50MB save to `~/Downloads/ClipSync/`, >50MB drop |
| File size | n/a | <50MB transfer, native Cmd+V/Ctrl+V paste at destination |
| Tray | none | menu bar (Mac) + system tray (Win), Pause/Resume/Quit |
| Auto-start | none | `launchd` (Mac) + `schtasks` (Win) |
| Notifications | none | `terminal-notifier`/`osascript` (Mac), `plyer`/BurntToast/`msg.exe` (Win) |
| Mac clipboard | `pbcopy`/`pbpaste` | `NSPasteboard` via pyobjc (file refs, PNG, TIFF) |
| Windows clipboard | `pyperclip` | `pyperclip` + `win32clipboard` `CF_HDROP` + `PIL.ImageGrab` |
| Echo prevention | text hash | content hash incl. saved-file path; ClipSync folder filtered from outgoing |
| Wire cap | small | 70MB (base64 overhead included) |

## Files

```
clipboard_server_v2.py    # Mac. NSPasteboard + Pillow. Listens 0.0.0.0:9999.
clipboard_client_v2.py    # Windows. pyperclip + win32clipboard + ImageGrab. Dials Mac, auto-reconnects.
clipsync_tray_mac.py      # Menu bar wrapper. --install registers launchd agent.
clipsync_tray_win.py      # System tray wrapper. --install registers schtasks ClipSync.
clipsync_debug.py         # Windows-side diagnostic (admin check, deps, schtasks, logs).
SETUP.md                  # Full install + auto-start guide.
requirements_mac.txt      # pystray, Pillow, pyobjc-*
requirements_win.txt      # pystray, Pillow, pyperclip, pywin32, plyer
```

## Run

See [SETUP.md](SETUP.md).

Short form:
```bash
# Mac
pip3 install -r requirements_mac.txt
python3 clipsync_tray_mac.py            # manual
python3 clipsync_tray_mac.py --install  # auto-start
```
```powershell
# Windows (NOT as admin — admin breaks tray + clipboard)
pip install -r requirements_win.txt
python clipsync_tray_win.py --host <MAC_IP>
python clipsync_tray_win.py --host <MAC_IP> --install
```

## Frozen — why

V3 replaces topology (Mac-server → Android-relay-server with N clients). V2 stays untouched as known-good fallback. Don't run V2 + V3 at same time on same machine — both fight for port 9999 on the listener side and clipboard hash state on either side. V3 install detects V2 launchd/schtasks entries and refuses.

## V3 TODO markers

Every transport extension point has a `V3 TODO:` comment. Search them when refactoring:
```bash
grep -n "V3 TODO" v2/*.py
```

## Limits (motivated V3)

- One client at a time
- Mac required (server hub)
- LAN only — no Tailscale config
- iPhone bridged only via Mac + Handoff
- No Android support
