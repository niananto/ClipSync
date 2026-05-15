#!/data/data/com.termux/files/usr/bin/sh
#
# boot-server.sh — Launch the ClipSync relay server inside proot Debian.
# Copy this file to ~/.termux/boot/ to start it at Android boot.
#
# Requires:
#   - Termux:Boot app installed (from F-Droid)
#   - proot-distro with a 'debian' install
#   - The ClipSync repo present inside the proot rootfs at /root/clipsync
#     (or whatever path you set in CLIPSYNC_DIR_IN_PROOT below)
#
# This script logs to ~/.clipsync-server-boot.log so you can see why it
# failed if it doesn't come up.

LOG=~/.clipsync-server-boot.log
CLIPSYNC_DIR_IN_PROOT=/root/clipsync

echo "[boot] $(date '+%Y-%m-%d %H:%M:%S') starting clipsync server in proot debian" >> "$LOG"
proot-distro login debian -- /bin/sh -c \
    "cd $CLIPSYNC_DIR_IN_PROOT && python3 clipsync_server.py" >> "$LOG" 2>&1 &

echo "[boot] $(date '+%Y-%m-%d %H:%M:%S') server pid=$!" >> "$LOG"
