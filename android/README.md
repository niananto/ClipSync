# ClipSync V3 — Android (Termux) setup

This phone runs two pieces:

| Piece  | Where                     | What it does                                  |
|--------|---------------------------|-----------------------------------------------|
| Server | proot Debian (Termux)     | TCP relay on port 9999. Fans out frames between clients. No clipboard work. |
| Client | Native Termux (no proot)  | Polls Android clipboard via Termux:API. Connects to `127.0.0.1:9999`. |

Why split: Termux:API binaries live in native Termux and reach the host
Android clipboard. proot can't see them cleanly. The relay server itself
doesn't touch any clipboard, so it can live anywhere — proot Debian is
convenient because Tailscale already runs there (alongside jellyfin).

## One-time install

### 1. Apps (from F-Droid, NOT Play Store)

- **Termux** (you have this already)
- **Termux:Boot** — runs scripts in `~/.termux/boot/` at Android boot
- **Termux:API** — gives `termux-clipboard-*` access to host Android clipboard

### 2. Termux packages

```sh
pkg update
pkg install python termux-api
termux-setup-storage   # gives Termux access to ~/storage/downloads
```

### 3. Get the repo

Native Termux (where the client runs):
```sh
cd ~
git clone <repo-url> clipsync
```

Inside proot Debian (where the server runs):
```sh
proot-distro login debian
apt update && apt install -y python3 git
cd /root
git clone <repo-url> clipsync
exit
```

### 4. Test once, manually

In one Termux session:
```sh
proot-distro login debian -- python3 /root/clipsync/clipsync_server.py
```
Should print `ClipSync V3 relay listening on 0.0.0.0:9999`.

In a second Termux session:
```sh
cd ~/clipsync
python3 clipsync_client.py --host 127.0.0.1
```
Should print `connecting to 127.0.0.1:9999` → `connected`.

Now copy some text on the phone. The server log shows `relay … from
client N → 0 peer(s)` (no other clients yet). When Mac/Win connect,
peer count will be > 0 and frames will flow.

### 5. Auto-start at boot

Copy the two boot scripts and make them executable:
```sh
mkdir -p ~/.termux/boot
cp ~/clipsync/android/boot-server.sh ~/.termux/boot/
cp ~/clipsync/android/boot-client.sh ~/.termux/boot/
chmod +x ~/.termux/boot/boot-server.sh ~/.termux/boot/boot-client.sh
```

Reboot the phone. Open the Termux:Boot app once after install so Android
grants it the right to run on boot.

## Logs

- Server (inside proot): `~/.clipsync/server.log` (proot home) — but the
  boot launcher tees stdout to `~/.clipsync-server-boot.log` in native
  Termux home for easier access.
- Client (native Termux): `~/.clipsync-client-boot.log`

```sh
tail -f ~/.clipsync-server-boot.log
tail -f ~/.clipsync-client-boot.log
```

## How peers reach the server

Other devices (Mac, Windows, other Androids) connect over Tailscale to
this phone's MagicDNS hostname or IP. Confirm:

```sh
tailscale status                 # shows this device's IP + name
tailscale ip -4                  # just the IPv4
```

Use that name/IP as `--host` on Mac/Win. Plain LAN IP also works if all
devices share Wi-Fi and you don't have Tailscale.

## Limits on Android side

- **Text only outbound.** Termux:API can't read images or file refs from
  the Android system clipboard.
- **Text + saved-file path inbound.** Images and files from peers are
  saved to `~/storage/downloads/ClipSync/` and the destination path is
  placed on the clipboard as text.
- **Phone must stay awake-ish.** Doze can suspend Termux. The Jellyfin
  setup already deals with this — same trick (battery optimisation
  disabled for Termux, optionally a foreground service via Termux:API
  notification) keeps the relay reachable.
