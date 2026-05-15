# ClipSync V1 — text-only LAN

Frozen reference. Original two-script proof-of-concept. Text only, no images, no files, no tray.

## What it does

Mac runs server. Windows runs client. Both poll their own clipboard every 500ms, hash text, send length-prefixed JSON frames over TCP port 9999. Echo prevented by `last_hash` check.

## Files

```
clipboard_server_v1.py    # Mac side. Uses pbcopy/pbpaste. Stdlib only.
clipboard_client_v1.py    # Windows side. Needs pyperclip.
```

## Run

Mac:
```bash
python3 clipboard_server_v1.py
```

Windows:
```powershell
pip install pyperclip
python clipboard_client_v1.py --host <MAC_LAN_IP>
```

## Wire format

```
[4-byte big-endian length][JSON UTF-8]
JSON: { "type": "text", "text": "..." }
```

## Why kept

Smallest readable baseline. Useful for debugging transport issues without image/file noise. Don't run alongside V2/V3 — same port (9999).

## Limits

- Text only
- LAN only (no Tailscale config)
- No tray, no auto-start
- One client at a time
- No encryption (V3 adds Tailscale binding instead)
