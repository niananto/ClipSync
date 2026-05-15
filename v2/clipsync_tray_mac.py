"""
clipsync_tray_mac.py — Menu bar tray app for ClipSync (Mac)
Wraps clipboard_server_v2 so you never need to touch a terminal.

Usage:
    python3 clipsync_tray_mac.py

    On first run, follow the prompt to set your config (port, etc.).
    On subsequent runs, config is loaded from ~/.clipsync.conf

Requirements:
    pip3 install pystray Pillow pyobjc-framework-Cocoa

    This file must live in the same directory as clipboard_server_v2.py

Auto-start on login:
    Run this once to install the launchd agent:
        python3 clipsync_tray_mac.py --install
    To uninstall:
        python3 clipsync_tray_mac.py --uninstall

Menu items:
    ● ClipSync — Active      (status indicator, not clickable)
    ─────────────────────
    Pause Sync               (pauses clipboard watching; connection stays alive)
    Resume Sync              (only shown when paused)
    ─────────────────────
    Open ClipSync Folder     (opens ~/Downloads/ClipSync in Finder)
    ─────────────────────
    Quit                     (stops sync and exits)

V3 TODO: Add "Connected / Disconnected" status based on whether a client is connected
V3 TODO: Load secret from ~/.clipsync.conf for HMAC handshake
"""

import argparse
import sys
import os
import threading
import time
import logging
import subprocess
from pathlib import Path
from io import BytesIO

# ── Tray icon drawing ──────────────────────────────────────────────────────────
# We draw the icon in code so there's no asset file to manage.
# Active = solid clipboard icon; Paused = same but greyed out.

from PIL import Image, ImageDraw, ImageFont

def _draw_icon(paused: bool = False) -> Image.Image:
    """
    Draw a simple clipboard icon.
    Active: white icon on dark teal background.
    Paused: white icon on grey background.
    """
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bg_color  = (100, 100, 100, 220) if paused else (30, 140, 120, 220)
    icon_color = (255, 255, 255, 200)

    # Background rounded rect (approximated with ellipse corners)
    draw.rounded_rectangle([2, 2, size - 2, size - 2], radius=10, fill=bg_color)

    # Clipboard body
    draw.rounded_rectangle([14, 20, 50, 56], radius=4, fill=None, outline=icon_color, width=3)

    # Clip at top
    draw.rounded_rectangle([22, 14, 42, 24], radius=4, fill=bg_color, outline=icon_color, width=3)

    # Lines on clipboard
    draw.line([20, 32, 44, 32], fill=icon_color, width=2)
    draw.line([20, 38, 44, 38], fill=icon_color, width=2)
    draw.line([20, 44, 36, 44], fill=icon_color, width=2)

    # Paused indicator: small "||" overlay in bottom-right
    if paused:
        draw.rectangle([38, 42, 42, 54], fill=(255, 200, 0, 230))
        draw.rectangle([44, 42, 48, 54], fill=(255, 200, 0, 230))

    return img


# ── Config ─────────────────────────────────────────────────────────────────────
CONFIG_PATH   = Path.home() / ".clipsync.conf"
CLIPSYNC_DIR  = Path.home() / "Downloads" / "ClipSync"
PLIST_DIR     = Path.home() / "Library" / "LaunchAgents"
PLIST_NAME    = "com.clipsync.server.plist"
LOG_DIR       = Path.home() / "Library" / "Logs" / "ClipSync"
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("clipsync.tray")


# ── launchd install / uninstall ────────────────────────────────────────────────

def _get_plist_content() -> str:
    python  = sys.executable
    script  = str(Path(__file__).resolve())
    log_out = str(LOG_DIR / "clipsync.log")
    log_err = str(LOG_DIR / "clipsync_err.log")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.clipsync.server</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script}</string>
    </array>

    <!-- Start automatically when you log in -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Restart if it crashes -->
    <key>KeepAlive</key>
    <true/>

    <!-- Log output -->
    <key>StandardOutPath</key>
    <string>{log_out}</string>
    <key>StandardErrorPath</key>
    <string>{log_err}</string>

    <!-- Give it your home environment -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
"""


def cmd_install():
    """Install the launchd agent so ClipSync starts on login."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_DIR.mkdir(parents=True, exist_ok=True)
    plist_path = PLIST_DIR / PLIST_NAME

    plist_path.write_text(_get_plist_content())
    subprocess.run(["launchctl", "load", "-w", str(plist_path)], check=False)
    print(f"✅ ClipSync installed as login agent.")
    print(f"   Plist:  {plist_path}")
    print(f"   Logs:   {LOG_DIR}/clipsync.log")
    print(f"   To stop without uninstalling: launchctl stop com.clipsync.server")
    print(f"   To uninstall: python3 {__file__} --uninstall")


def cmd_uninstall():
    """Remove the launchd agent."""
    plist_path = PLIST_DIR / PLIST_NAME
    if plist_path.exists():
        subprocess.run(["launchctl", "unload", "-w", str(plist_path)], check=False)
        plist_path.unlink()
        print("✅ ClipSync login agent removed.")
    else:
        print("ℹ️  No launchd agent found — nothing to remove.")


# ── Sync engine (thin wrapper around clipboard_server_v2) ──────────────────────

# Add script directory to path so we can import the server module
sys.path.insert(0, str(Path(__file__).parent))

try:
    import clipboard_server_v2 as _server
    SERVER_AVAILABLE = True
except ImportError as e:
    SERVER_AVAILABLE = False
    log.error(f"Could not import clipboard_server_v2: {e}")
    log.error("Make sure clipsync_tray_mac.py and clipboard_server_v2.py are in the same folder.")


class SyncEngine:
    """
    Owns the shared_state and all sync threads.
    The tray calls pause() / resume() / stop() on this.
    """

    def __init__(self):
        self._paused   = False
        self._stopped  = False
        self._lock     = threading.Lock()
        self.shared_state: dict = {}

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def stopped(self) -> bool:
        return self._stopped

    def pause(self):
        with self._lock:
            self._paused = True
            self.shared_state["paused"] = True
        log.info("Sync paused")

    def resume(self):
        with self._lock:
            self._paused = False
            self.shared_state["paused"] = False
        log.info("Sync resumed")

    def stop(self):
        with self._lock:
            self._stopped = True
            self.shared_state["stopped"] = True
        log.info("Sync stopping")

    def start(self):
        """Start server socket + clipboard watcher in background threads."""
        if not SERVER_AVAILABLE:
            log.error("Server module not available — sync will not run")
            return

        initial = _server.read_clipboard()
        self.shared_state = {
            "last_hash": _server.hash_payload(initial) if initial else "",
            "conn":      None,
            "paused":    False,
            "stopped":   False,
        }

        # Clipboard watcher — patched to respect paused/stopped flags
        def watcher_loop():
            log.info("Clipboard watcher started")
            while not self.shared_state.get("stopped"):
                time.sleep(_server.POLL_INTERVAL)
                if self.shared_state.get("paused"):
                    continue

                payload = _server.read_clipboard()
                if payload is None:
                    continue

                current_hash = _server.hash_payload(payload)
                if current_hash == self.shared_state.get("last_hash"):
                    continue

                self.shared_state["last_hash"] = current_hash
                conn = self.shared_state.get("conn")
                if not conn:
                    continue

                t = payload["type"]
                if t == "text":
                    log.info(f"→ Text to Windows ({len(payload['text'])} chars)")
                elif t == "image":
                    size = len(payload["png_bytes"])
                    if size > _server.IMAGE_FILE_MAX:
                        log.warning(f"Image too large ({size / 1e6:.1f}MB) — skipping")
                        continue
                    log.info(f"→ Image to Windows ({size / 1e6:.1f}MB)")
                elif t == "file":
                    log.info(f"→ File to Windows: {payload['name']}")

                ok = _server.send_payload(conn, payload)
                if not ok:
                    log.warning("Send failed — client may have disconnected")

        # Socket server — accepts one client at a time
        def server_loop():
            import socket
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((_server.HOST, _server.PORT))
            srv.listen(1)
            srv.settimeout(1.0)  # So we can check stopped flag periodically
            log.info(f"ClipSync listening on port {_server.PORT}")
            log.info(f"ClipSync folder: {_server.CLIPSYNC_DIR}")

            while not self.shared_state.get("stopped"):
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                except Exception:
                    break

                # Wrap handle_client to also check paused flag on incoming messages
                def handle(conn=conn, addr=addr):
                    log.info(f"Windows connected: {addr}")
                    self.shared_state["conn"] = conn
                    try:
                        while not self.shared_state.get("stopped"):
                            payload = _server.decode_message(conn)
                            if payload is None:
                                log.info(f"Client disconnected: {addr}")
                                break
                            if self.shared_state.get("paused"):
                                continue  # Drop incoming while paused
                            new_hash = _server.apply_incoming(payload, self.shared_state)
                            self.shared_state["last_hash"] = new_hash
                    finally:
                        self.shared_state["conn"] = None
                        conn.close()

                threading.Thread(target=handle, daemon=True).start()

            srv.close()

        threading.Thread(target=watcher_loop, daemon=True).start()
        threading.Thread(target=server_loop,  daemon=True).start()


# ── Tray icon ──────────────────────────────────────────────────────────────────

def run_tray(engine: SyncEngine):
    try:
        import pystray
    except ImportError:
        log.error("pystray not installed. Run: pip3 install pystray")
        # Fall back to running headless (no tray, Ctrl+C to quit)
        log.info("Running headless — press Ctrl+C to quit")
        try:
            while not engine.stopped:
                time.sleep(1)
        except KeyboardInterrupt:
            engine.stop()
        return

    icon_ref: list = [None]  # mutable container so callbacks can reference icon

    def update_icon():
        if icon_ref[0]:
            icon_ref[0].icon  = _draw_icon(paused=engine.paused)
            icon_ref[0].title = "ClipSync — Paused" if engine.paused else "ClipSync — Active"
            icon_ref[0].update_menu()

    def on_pause_resume(icon, item):
        if engine.paused:
            engine.resume()
        else:
            engine.pause()
        update_icon()

    def on_open_folder(icon, item):
        CLIPSYNC_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(CLIPSYNC_DIR)])

    def on_quit(icon, item):
        engine.stop()
        icon.stop()

    def make_menu():
        status_text = "⏸ Paused" if engine.paused else "● Active"
        toggle_text = "Resume Sync" if engine.paused else "Pause Sync"
        return pystray.Menu(
            pystray.MenuItem(f"ClipSync — {status_text}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(toggle_text, on_pause_resume),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open ClipSync Folder", on_open_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_quit),
        )

    icon = pystray.Icon(
        name="ClipSync",
        icon=_draw_icon(paused=False),
        title="ClipSync — Active",
        menu=pystray.Menu(
            pystray.MenuItem("ClipSync — ● Active", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Pause Sync",           on_pause_resume),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open ClipSync Folder", on_open_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit",                 on_quit),
        ),
    )
    icon_ref[0] = icon

    # Rebuild menu dynamically so Pause↔Resume label flips correctly
    icon.menu = pystray.Menu(lambda: make_menu().items)

    log.info("Tray icon started — look for the clipboard icon in your menu bar")
    icon.run()  # Blocks until icon.stop() is called


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ClipSync Tray — Mac")
    parser.add_argument("--install",   action="store_true", help="Install as login agent and exit")
    parser.add_argument("--uninstall", action="store_true", help="Remove login agent and exit")
    args = parser.parse_args()

    if args.install:
        cmd_install()
        return
    if args.uninstall:
        cmd_uninstall()
        return

    if not SERVER_AVAILABLE:
        sys.exit(
            "ERROR: clipboard_server_v2.py not found in the same directory.\n"
            "Place both files in the same folder and try again."
        )

    engine = SyncEngine()
    engine.start()
    run_tray(engine)


if __name__ == "__main__":
    main()
