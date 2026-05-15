#!/data/data/com.termux/files/usr/bin/sh
#
# boot-client.sh — Launch the ClipSync client inside *native* Termux
# (not proot), so termux-clipboard-* can reach the host Android clipboard.
# Copy this file to ~/.termux/boot/ to start it at Android boot.
#
# Requires:
#   - Termux:Boot app installed (from F-Droid)
#   - Termux:API app installed (from F-Droid) + pkg install termux-api
#   - Python in Termux: pkg install python
#   - The ClipSync repo cloned to ~/clipsync (or whatever you set below)
#   - Tailscale running on this device (so the client can reach itself
#     via 127.0.0.1, OR via the device's Tailscale IP — pick whichever is
#     simpler). 127.0.0.1 works because proot shares the host network.
#
# This script logs to ~/.clipsync-client-boot.log.

LOG=~/.clipsync-client-boot.log
CLIPSYNC_DIR=~/clipsync
SERVER_HOST=127.0.0.1     # server is on this same device, inside proot

echo "[boot] $(date '+%Y-%m-%d %H:%M:%S') starting clipsync client" >> "$LOG"
cd "$CLIPSYNC_DIR" || { echo "[boot] missing $CLIPSYNC_DIR" >> "$LOG"; exit 1; }

# Wait briefly for the server to come up (it's launched by boot-server.sh
# in parallel; this is order-independent but reduces "connection refused"
# noise in the log on cold boot).
sleep 10

python3 clipsync_client.py --host "$SERVER_HOST" >> "$LOG" 2>&1 &
echo "[boot] $(date '+%Y-%m-%d %H:%M:%S') client pid=$!" >> "$LOG"
