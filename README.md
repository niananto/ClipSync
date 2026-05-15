# ClipSync

LAN / Tailscale clipboard sync between Windows, Mac, Android (and iPhone via Mac+Handoff). No cloud account, no third-party service, direct TCP. Python only.

```
Windows ─┐
         │
Mac ─────┼──> Android (relay) <── other Android client
         │
iPhone ──┘  (bridged via Mac + Apple Universal Clipboard)
```

## Three versions in this repo

| Version | Location           | Topology                                    | Scope                              |
|---------|--------------------|---------------------------------------------|------------------------------------|
| V1      | [`v1/`](v1/)       | Mac server ↔ Windows client                 | Text only. Stdlib only.            |
| V2      | [`v2/`](v2/)       | Mac server ↔ Windows client                 | Text, images, files, tray, auto-start. LAN only. |
| V3      | (this root folder) | Android relay ← Mac/Win/Android clients     | Multi-client fan-out. Tailscale or LAN. |

V1 and V2 are frozen, kept for reference and as known-good fallbacks. V3 is the active version.

## V3 in 60 seconds

- **Android phone** runs a relay server (in Termux+proot Debian, alongside Tailscale/Jellyfin). Pure fan-out; never touches its own clipboard from the server.
- **Mac, Windows, and (separately) native Termux on Android** run clients. Each polls its local clipboard every 500ms and pushes changes to the relay; relay broadcasts to every other connected client.
- **iPhone** bridges through the Mac via Apple's Universal Clipboard (Handoff) — same trick as V2. No native iOS app.
- **Transport**: plain TCP port 9999. Encryption + access control come from Tailscale (Wireguard + ACLs). Works on LAN too, just without those guarantees.

## V3 quick start

See [SETUP.md](SETUP.md) for the full walkthrough. Short version:

**Android (server + client, one device):** see [android/README.md](android/README.md).

**Mac (client):**
```bash
pip3 install -r requirements_mac.txt
python3 clipsync_tray_mac.py --host phone.tail-xxxx.ts.net          # first run
python3 clipsync_tray_mac.py --host phone.tail-xxxx.ts.net --install # auto-start
```

**Windows (client, NOT as admin):**
```powershell
pip install -r requirements_win.txt
python clipsync_tray_win.py --host phone.tail-xxxx.ts.net
python clipsync_tray_win.py --host phone.tail-xxxx.ts.net --install
```

`--host` accepts: Tailscale MagicDNS name, Tailscale IPv4, LAN IP, or any hostname `socket.getaddrinfo` can resolve.

## V3 collision with V2

V3 install scripts **refuse** to run on top of a V2 install. They detect:
- Mac: `~/Library/LaunchAgents/com.clipsync.server.plist` or a loaded `com.clipsync.server` agent
- Windows: scheduled task named `ClipSync`

If detected, the installer prints the exact commands to uninstall V2 and exits. V3 uses new names (`com.clipsync.v3.client` / `ClipSyncV3`) so the two never share an identifier.

If you want to keep V2 running on a side machine while V3 runs on others, that's fine — only the same machine can't host both at once (they'd race on the clipboard).

## V3 file layout

```
clipsync_server.py            # relay (run on Android in proot Debian; stdlib only)
clipsync_client.py            # client core: transport, framing, watcher loop
clipboard_mac.py              # platform clipboard: NSPasteboard
clipboard_win.py              # platform clipboard: win32clipboard + CF_HDROP
clipboard_android.py          # platform clipboard: termux-clipboard-get/set
clipsync_tray_mac.py          # menu-bar wrapper; --install / --uninstall
clipsync_tray_win.py          # system-tray wrapper; --install / --uninstall
clipsync_debug.py             # cross-platform diagnostic
android/
  boot-server.sh              # ~/.termux/boot/ launcher for the relay
  boot-client.sh              # ~/.termux/boot/ launcher for the client
  README.md                   # Android-specific setup
requirements_mac.txt
requirements_win.txt
requirements_android.txt
v1/                           # frozen V1
v2/                           # frozen V2 (still works standalone)
```

## How it works

**Wire format** (unchanged from V2):
```
[4-byte big-endian length][JSON UTF-8]
text:  { "type": "text",  "text": "..." }
image: { "type": "image", "data": "<base64 PNG>", "size": N }
file:  { "type": "file",  "name": "x.png", "data": "<base64>", "size": N }
```

**Relay**: server accepts N clients, assigns each an id, and on every incoming frame rebroadcasts the raw bytes to all *other* connected clients. No JSON parsing on the relay path — frames are passed through unmodified.

**Echo prevention**: each client tracks `last_hash` of its own clipboard. Before writing an incoming frame to the clipboard, it sets `last_hash` to the new content's hash so the next poll won't re-emit. Saved files (>10MB images, file payloads) hash the destination *path* instead of the bytes, so the file ref placed on the clipboard doesn't loop. Belt-and-braces: the relay also never sends a frame back to the client that sent it.

**Size policy** (same as V2):

| Type            | Behaviour                                              |
|-----------------|--------------------------------------------------------|
| Text            | always synced                                          |
| Image <10 MB    | clipboard image directly                               |
| Image 10–50 MB  | saved to `~/Downloads/ClipSync/`, path on clipboard    |
| Image >50 MB    | dropped with warning                                   |
| File <50 MB     | bytes transferred, native paste (Cmd+V / Ctrl+V) works |
| File >50 MB     | dropped with warning                                   |

Android client is text-only outbound (Termux:API doesn't expose system-clipboard images or file refs). Inbound images and files still land in `~/storage/downloads/ClipSync/` and the path is pushed to the clipboard as text.

## iPhone

No native ClipSync on iOS. Apple sandbox blocks any background clipboard write from a non-Apple app, so this is unfixable without an iOS app. Bridge via Mac + Handoff like in V2: the Mac is a V3 client and the iPhone shares its clipboard with the Mac through Apple's Universal Clipboard.

If you need iPhone → others without a Mac in the room, the only practical option is a manual iOS Shortcut that POSTs to the relay over Tailscale on share-sheet trigger. Out of scope for now.

## Security

V3 has no app-layer encryption or authentication. It relies on Tailscale (Wireguard + ACLs) for both. **Do not expose port 9999 to the public internet.** If you must run V3 over plain LAN with untrusted devices on the same network, add TLS + HMAC on top — the framing was kept compatible with V2 specifically so this can land without breaking the wire format.

## Troubleshooting

```bash
python3 clipsync_debug.py    # Mac
python clipsync_debug.py     # Windows
```

For Android:
```sh
tail -f ~/.clipsync-server-boot.log
tail -f ~/.clipsync-client-boot.log
```

## Roadmap

| Version | Status      | What                                                                 |
|---------|-------------|----------------------------------------------------------------------|
| V1      | ✅ Frozen   | Text only, LAN                                                       |
| V2      | ✅ Frozen   | Images, files, tray, auto-start                                      |
| V3      | 🚧 Active   | Multi-client mesh, Android relay, Tailscale                          |
| Future  | 💭 Maybe    | Optional TLS+HMAC for non-Tailscale users; iOS Shortcut recipe; Wake-on-LAN for sleeping clients |
