# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

ClipSync — clipboard sync between Mac, Windows, Android, and iPhone (iPhone via Mac+Handoff). Three versions in this repo, organized by folder. V1 and V2 are frozen reference implementations. V3 is the active version and lives in the root.

No tests, no build, no lint. Pure Python scripts run directly.

## Repo layout

```
<root>/             V3 — active. Multi-client mesh, Android relay, Tailscale.
  clipsync_server.py        relay (stdlib only; runs in Termux+proot Debian on phone)
  clipsync_client.py        shared client core (transport, framing, watcher loop, reconnect)
  clipboard_{mac,win,android}.py    platform clipboard adapters
  clipsync_tray_{mac,win}.py        pystray wrappers with --install / --uninstall
  clipsync_debug.py         cross-platform diagnostic
  android/                  boot scripts + Android setup README
v1/   text-only baseline, stdlib only. Frozen.
v2/   Mac server + Win client, images/files/tray/auto-start. Frozen. Standalone (imports stay relative to v2/).
```

## Running V3

Android (server + own client, in Termux on the phone) — see [android/README.md](android/README.md). Server runs inside proot Debian alongside the existing tailscale+jellyfin setup; client runs in native Termux so `termux-clipboard-*` reaches the host clipboard.

Mac (client only):
```bash
pip3 install -r requirements_mac.txt
python3 clipsync_tray_mac.py --host phone.tail-xxxx.ts.net
python3 clipsync_tray_mac.py --host <addr> --install   # launchd auto-start
python3 clipsync_tray_mac.py --uninstall
python3 clipsync_client.py --host <addr>               # no tray
```

Windows (client only, NEVER as admin):
```powershell
pip install -r requirements_win.txt
python clipsync_tray_win.py --host phone.tail-xxxx.ts.net
python clipsync_tray_win.py --host <addr> --install    # schtasks auto-start
python clipsync_tray_win.py --uninstall
python clipsync_debug.py                               # diagnostic
```

`--host` accepts: Tailscale MagicDNS name, Tailscale IPv4, LAN IP, hostname — anything `socket.getaddrinfo` resolves. Config persisted at `~/.clipsync.conf` (Mac, JSON) / `%APPDATA%\ClipSync\clipsync.conf` (Windows, JSON; reads V2 `k=v` format for back-compat).

Logs: Mac `~/Library/Logs/ClipSync/clipsync_v3.log`. Windows `%APPDATA%\ClipSync\clipsync_v3.log`. Android `~/.clipsync-{server,client}-boot.log` (native Termux home).

## V3 architecture

**Topology.** Hub-and-spoke. Android phone = relay-only server. Mac, Windows, native-Termux-on-Android = clients. The server never reads or writes its own clipboard — that's done by a separate client process on Android (split because Termux:API can't be reached cleanly from inside proot).

**Relay (`clipsync_server.py`).** Threaded TCP accept loop. Each connect goes into a `Hub` dict `{client_id: socket}` guarded by a lock. On each incoming length-prefixed frame the raw bytes are rebroadcast to every other connected client — the server doesn't parse the JSON body. The server never sends a frame back to its originator (echo guard #1). Stdlib only.

**Client core (`clipsync_client.py`).** `ClipSyncClient(host, port, clipboard_module, paused, stop)` owns two threads: a watcher that polls the clipboard every 500ms, and a receiver/connect loop with reconnect. Tray wrappers construct one and toggle the `paused` / `stop` `threading.Event`s from menu callbacks. Standalone `python clipsync_client.py --host …` auto-selects the platform clipboard module (`clipboard_mac` / `clipboard_win` / `clipboard_android`) by `sys.platform`.

**Clipboard module contract.** Each platform module exposes the same names — see the `ClipboardModule` Protocol in `clipsync_client.py`:
```
CLIPSYNC_DIR : Path
read_clipboard() -> dict | None
write_clipboard_text(s)
write_clipboard_image(png_bytes)
write_clipboard_fileref(path)
notify(title, message)
```
`read_clipboard()` returns `{"type": "text"|"image"|"file", ...}` or `None`. The core treats every module the same; platform-specific quirks (Mac NSPasteboard, Windows `CF_HDROP`, Android `termux-clipboard-*`) are isolated inside the module.

**Echo prevention (echo guard #2).** Each client tracks `last_hash` of clipboard content. Before writing an incoming payload, it sets `last_hash` to the new content's hash so the next poll won't re-emit. For saved-to-folder cases (image >10MB, file payloads) the hash is over the *destination path* string, not the bytes, so the native file ref placed on the clipboard doesn't loop. Each platform module also skips reads originating inside `CLIPSYNC_DIR`.

**Wire format** (unchanged from V2 — kept compatible deliberately):
```
[4-byte big-endian length][JSON UTF-8]
text:  { "type": "text",  "text": "..." }
image: { "type": "image", "data": "<base64 PNG>", "size": N }
file:  { "type": "file",  "name": "x.png", "data": "<base64>", "size": N }
```
70MB frame cap. Size policy on payloads: text always; image <10MB → clipboard; 10–50MB → save to `~/Downloads/ClipSync/`, path on clipboard; >50MB drop. File <50MB → bytes + native paste; >50MB drop. Size constants (`IMAGE_INLINE_MAX`, `IMAGE_FILE_MAX`, `FILE_MAX`) live in `clipsync_client.py` only — platform modules no longer duplicate them.

**Android-specific limits.** Termux:API exposes only text on the system clipboard. Outbound from Android = text only. Inbound: images/files are saved to `~/storage/downloads/ClipSync/` and the destination path is written as text to the clipboard. `clipboard_android.py` detects whether `termux-clipboard-get` is on PATH and degrades gracefully if Termux:API isn't installed.

**Transport security.** None at app layer — V3 binds plain TCP and delegates encryption + access control to Tailscale (Wireguard + ACLs). The relay binds `0.0.0.0:9999` and trusts the network stack to gate access. Plain LAN works too; user accepts the trade-off.

## V2 collision handling

V3 tray scripts at `--install` detect V2 installs and refuse rather than silently replacing. V3 uses distinct identifiers everywhere so they can't be confused:

| Slot          | V2                                                     | V3                                       |
|---------------|--------------------------------------------------------|------------------------------------------|
| Mac launchd   | label `com.clipsync.server` / `com.clipsync.server.plist` | `com.clipsync.v3.client` / `com.clipsync.v3.client.plist` |
| Windows task  | `ClipSync`                                             | `ClipSyncV3`                             |
| Logs          | `clipsync.log`                                         | `clipsync_v3.log`                        |

`detect_v2()` in each tray script checks for the V2 plist/task and prints exact uninstall commands. The same checks fire in `clipsync_debug.py`.

## Conventions to preserve

- **V1/V2 are frozen.** Don't edit them. They serve as known-good fallbacks and a reference for transport-layer history. V2 still imports its sibling modules by simple name (`clipboard_server_v2`, `clipboard_client_v2`) — Python finds them via the script's own dir, so the move into `v2/` didn't break anything.
- **Wire format compatibility.** V3's framing is byte-compatible with V2 by design (so a future V4 can add TLS+HMAC on top without redoing the schema). When touching `encode_message` / `decode_message`, keep that compatibility.
- **No app-layer crypto in V3.** Tailscale provides encryption + auth. If TLS/HMAC ever lands, do it as wrapper around the existing transport, not as a frame-format change.
- **One source of truth for size caps.** Constants live in `clipsync_client.py`. Don't duplicate them in platform modules.
- **Android = two processes.** Server in proot Debian, client in native Termux. Don't try to merge — Termux:API can't be reached cleanly from inside proot.
