"""
clipsync_tray_mac.py — Menu bar tray for ClipSync V3 (Mac client).

Wraps clipsync_client.ClipSyncClient with a pystray menu bar icon.
Mac is now a *client* — it dials the Android relay server. Pair with
Universal Clipboard / Handoff to bridge iPhone via the Mac.

Usage:
    python3 clipsync_tray_mac.py --host <server>          # first run
    python3 clipsync_tray_mac.py                          # subsequent (uses ~/.clipsync.conf)
    python3 clipsync_tray_mac.py --host <server> --install
    python3 clipsync_tray_mac.py --uninstall

--host accepts an IP, a LAN hostname, or a Tailscale MagicDNS name
(e.g. phone.tail-xxxx.ts.net).

V2 collision: --install detects V2's launchd agent and refuses with
exact uninstall instructions rather than silently replacing it.

Requirements:
    pip3 install pystray Pillow pyobjc-framework-Cocoa

This file must live in the same directory as clipsync_client.py and
clipboard_mac.py.
"""

import argparse
import json
import logging
import subprocess
import sys
import threading
from pathlib import Path

from PIL import Image, ImageDraw

# ── Config paths ───────────────────────────────────────────────────────────────
CONFIG_PATH    = Path.home() / ".clipsync.conf"
PLIST_DIR      = Path.home() / "Library" / "LaunchAgents"
V3_PLIST_NAME  = "com.clipsync.v3.client.plist"
V3_PLIST_LABEL = "com.clipsync.v3.client"
V2_PLIST_NAME  = "com.clipsync.server.plist"
V2_PLIST_LABEL = "com.clipsync.server"
LOG_DIR        = Path.home() / "Library" / "Logs" / "ClipSync"
DEFAULT_PORT   = 9999
# ───────────────────────────────────────────────────────────────────────────────

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler(LOG_DIR / "clipsync_v3.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("clipsync.tray.mac")

# Make sibling modules importable when launchd runs us from /
sys.path.insert(0, str(Path(__file__).resolve().parent))

import clipboard_mac as cb
from clipsync_client import ClipSyncClient


# ── Icon ───────────────────────────────────────────────────────────────────────

def _draw_icon(paused: bool = False) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bg = (100, 100, 100, 220) if paused else (30, 140, 120, 220)
    fg = (255, 255, 255, 220)
    draw.rounded_rectangle([2, 2, size - 2, size - 2], radius=10, fill=bg)
    draw.rounded_rectangle([14, 20, 50, 56], radius=4, outline=fg, width=3)
    draw.rounded_rectangle([22, 14, 42, 24], radius=4, fill=bg, outline=fg, width=3)
    draw.line([20, 32, 44, 32], fill=fg, width=2)
    draw.line([20, 38, 44, 38], fill=fg, width=2)
    draw.line([20, 44, 36, 44], fill=fg, width=2)
    if paused:
        draw.rectangle([38, 42, 42, 54], fill=(255, 200, 0, 230))
        draw.rectangle([44, 42, 48, 54], fill=(255, 200, 0, 230))
    return img


# ── Config file ────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ── V2 collision detection ─────────────────────────────────────────────────────

def detect_v2() -> bool:
    """True if V2's launchd agent is installed."""
    v2_plist = PLIST_DIR / V2_PLIST_NAME
    if v2_plist.exists():
        return True
    try:
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        return V2_PLIST_LABEL in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def print_v2_uninstall_instructions() -> None:
    v2_plist = PLIST_DIR / V2_PLIST_NAME
    print("ERROR: ClipSync V2 is already installed as a login agent.")
    print("V3 won't install on top of it — uninstall V2 first, then rerun --install.")
    print()
    print("Run these commands:")
    print(f"  launchctl unload {v2_plist}")
    print(f"  rm {v2_plist}")
    print()
    print("Or if you still have V2's tray script around:")
    print("  python3 v2/clipsync_tray_mac.py --uninstall")


# ── launchd install / uninstall ────────────────────────────────────────────────

def _plist_xml(host: str, port: int) -> str:
    python  = sys.executable
    script  = str(Path(__file__).resolve())
    log_out = str(LOG_DIR / "clipsync_v3.log")
    log_err = str(LOG_DIR / "clipsync_v3.err.log")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{V3_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script}</string>
        <string>--host</string>
        <string>{host}</string>
        <string>--port</string>
        <string>{port}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_out}</string>
    <key>StandardErrorPath</key>
    <string>{log_err}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
"""


def cmd_install(host: str, port: int) -> None:
    if detect_v2():
        print_v2_uninstall_instructions()
        sys.exit(2)

    PLIST_DIR.mkdir(parents=True, exist_ok=True)
    plist_path = PLIST_DIR / V3_PLIST_NAME

    save_config({"host": host, "port": port})
    plist_path.write_text(_plist_xml(host, port))
    subprocess.run(["launchctl", "load", "-w", str(plist_path)], check=False)
    print(f"✅ ClipSync V3 installed as login agent.")
    print(f"   Plist:  {plist_path}")
    print(f"   Config: {CONFIG_PATH}")
    print(f"   Logs:   {LOG_DIR / 'clipsync_v3.log'}")
    print(f"   Stop:   launchctl stop {V3_PLIST_LABEL}")
    print(f"   Uninstall: python3 {__file__} --uninstall")


def cmd_uninstall() -> None:
    plist_path = PLIST_DIR / V3_PLIST_NAME
    if plist_path.exists():
        subprocess.run(["launchctl", "unload", "-w", str(plist_path)], check=False)
        plist_path.unlink()
        print("✅ ClipSync V3 login agent removed.")
    else:
        print("ℹ️  No V3 launchd agent found.")


# ── Tray runtime ───────────────────────────────────────────────────────────────

def run_tray(client: ClipSyncClient) -> None:
    try:
        import pystray
    except ImportError:
        log.error("pystray not installed. Run: pip3 install pystray")
        log.info("Running headless — Ctrl+C to quit")
        try:
            client.wait()
        except KeyboardInterrupt:
            client.shutdown()
        return

    icon_ref: list = [None]

    def refresh():
        if icon_ref[0]:
            icon_ref[0].icon  = _draw_icon(paused=client.paused.is_set())
            icon_ref[0].title = "ClipSync V3 — Paused" if client.paused.is_set() else "ClipSync V3 — Active"
            icon_ref[0].update_menu()

    def on_pause(icon, item):
        if client.paused.is_set():
            client.paused.clear()
            log.info("resumed")
        else:
            client.paused.set()
            log.info("paused")
        refresh()

    def on_open(icon, item):
        cb.CLIPSYNC_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(cb.CLIPSYNC_DIR)])

    def on_quit(icon, item):
        client.shutdown()
        icon.stop()

    def make_menu():
        status = "⏸ Paused" if client.paused.is_set() else "● Active"
        toggle = "Resume Sync" if client.paused.is_set() else "Pause Sync"
        return pystray.Menu(
            pystray.MenuItem(f"ClipSync V3 — {status}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(toggle, on_pause),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open ClipSync Folder", on_open),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_quit),
        )

    icon = pystray.Icon(
        name="ClipSync V3",
        icon=_draw_icon(paused=False),
        title="ClipSync V3 — Active",
        menu=pystray.Menu(lambda: make_menu().items),
    )
    icon_ref[0] = icon
    log.info("tray started — look for the clipboard icon in the menu bar")
    icon.run()


# ── Entry ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ClipSync V3 tray (Mac client)")
    parser.add_argument("--host", help="server address (IP, hostname, or Tailscale MagicDNS name)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--install",   action="store_true", help="install login agent and exit")
    parser.add_argument("--uninstall", action="store_true", help="remove login agent and exit")
    args = parser.parse_args()

    if args.uninstall:
        cmd_uninstall()
        return

    cfg = load_config()
    host = args.host or cfg.get("host")
    port = args.port if args.port != DEFAULT_PORT else cfg.get("port", DEFAULT_PORT)

    if args.install:
        if not host:
            sys.exit("--install requires --host on first run")
        cmd_install(host, port)
        return

    if not host:
        sys.exit("No --host given and ~/.clipsync.conf missing. Run with --host <server>.")

    # Persist host so future runs (and launchd) don't need flags
    save_config({"host": host, "port": port})

    paused = threading.Event()
    stop   = threading.Event()
    client = ClipSyncClient(host, port, cb, paused=paused, stop=stop)
    client.start()
    run_tray(client)


if __name__ == "__main__":
    main()
