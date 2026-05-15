"""
clipsync_tray_win.py — System tray app for ClipSync (Windows)
Wraps clipboard_client_v2 so you never need to touch a terminal.

Usage:
    python clipsync_tray_win.py --host <MAC_IP>

    Example:
        python clipsync_tray_win.py --host 192.168.1.42

    On first run the host is saved to %APPDATA%\\ClipSync\\clipsync.conf
    so subsequent runs (including auto-start) don't need --host.

Requirements:
    pip install pystray Pillow pyperclip pywin32

    This file must live in the same directory as clipboard_client_v2.py

Auto-start on login:
    Run this once (as a normal user, not admin):
        python clipsync_tray_win.py --host 192.168.1.42 --install
    To uninstall:
        python clipsync_tray_win.py --uninstall

Tray menu:
    ClipSync — Active        (status, not clickable)
    ───────────────────────
    Pause Sync
    Resume Sync              (only when paused)
    ───────────────────────
    Open ClipSync Folder
    ───────────────────────
    Quit

V3 TODO: Add secret to config for HMAC handshake
V3 TODO: Show Connected/Disconnected status in tray menu
"""

import argparse
import sys
import os
import threading
import time
import logging
import subprocess
import winreg
from pathlib import Path

from PIL import Image, ImageDraw

# ── Config paths ───────────────────────────────────────────────────────────────
APPDATA       = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
CONFIG_DIR    = APPDATA / "ClipSync"
CONFIG_FILE   = CONFIG_DIR / "clipsync.conf"
LOG_FILE      = CONFIG_DIR / "clipsync.log"

def _get_downloads_dir() -> Path:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as k:
            val, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
            return Path(val)
    except Exception:
        return Path.home() / "Downloads"

CLIPSYNC_DIR  = _get_downloads_dir() / "ClipSync"
# ───────────────────────────────────────────────────────────────────────────────

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CLIPSYNC_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("clipsync.tray")


# ── Icon drawing ───────────────────────────────────────────────────────────────

def _draw_icon(paused: bool = False) -> Image.Image:
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bg_color   = (100, 100, 100, 220) if paused else (30, 140, 120, 220)
    icon_color = (255, 255, 255, 200)

    draw.rounded_rectangle([2, 2, size - 2, size - 2], radius=10, fill=bg_color)
    draw.rounded_rectangle([14, 20, 50, 56], radius=4, fill=None, outline=icon_color, width=3)
    draw.rounded_rectangle([22, 14, 42, 24], radius=4, fill=bg_color, outline=icon_color, width=3)
    draw.line([20, 32, 44, 32], fill=icon_color, width=2)
    draw.line([20, 38, 44, 38], fill=icon_color, width=2)
    draw.line([20, 44, 36, 44], fill=icon_color, width=2)

    if paused:
        draw.rectangle([38, 42, 42, 54], fill=(255, 200, 0, 230))
        draw.rectangle([44, 42, 48, 54], fill=(255, 200, 0, 230))

    return img


# ── Config read/write ──────────────────────────────────────────────────────────

def load_config() -> dict:
    config = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                config[k.strip()] = v.strip()
    return config


def save_config(config: dict):
    CONFIG_FILE.write_text("\n".join(f"{k}={v}" for k, v in config.items()))


# ── Task Scheduler install / uninstall ─────────────────────────────────────────

TASK_NAME = "ClipSync"

def cmd_install(host: str):
    """
    Register ClipSync as a Task Scheduler logon task for the current user.

    IMPORTANT: Run this as your NORMAL user account, NOT as administrator.
    Admin elevation causes the task to run in a non-interactive session,
    which has no desktop — meaning no tray icon and no clipboard access.

    The task uses LogonType=InteractiveToken so it always runs in your
    desktop session where the tray and clipboard are available.
    """
    save_config({"host": host})

    # pythonw.exe is identical to python.exe but suppresses the console window —
    # no terminal will flash on screen at login. It lives next to python.exe.
    python_w = Path(sys.executable).with_name(
        Path(sys.executable).name.replace("python.exe", "pythonw.exe")
    )
    python = str(python_w) if python_w.exists() else sys.executable
    script = str(Path(__file__).resolve())


    # Get current username in DOMAIN\user format for the Principal block
    current_user = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")

    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <!-- Only trigger for this specific user -->
      <UserId>{current_user}</UserId>
    </LogonTrigger>
  </Triggers>

  <Principals>
    <Principal id="Author">
      <UserId>{current_user}</UserId>
      <!--
        InteractiveToken is the critical setting.
        It forces the task to run in your interactive desktop session,
        which is the only place a tray icon and clipboard access work.
        Without this, Windows runs the task in a headless background session.
      -->
      <LogonType>InteractiveToken</LogonType>
      <!-- RunLevel LeastPrivilege = no UAC elevation = runs as normal user -->
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>

  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <!-- Restart up to 999 times if it crashes, waiting 1 min between attempts -->
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>

  <Actions>
    <Exec>
      <Command>{python}</Command>
      <Arguments>"{script}"</Arguments>
      <WorkingDirectory>{Path(script).parent}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""

    xml_path = CONFIG_DIR / "clipsync_task.xml"
    xml_path.write_text(xml, encoding="utf-16")

    result = subprocess.run(
        ["schtasks", "/create", "/tn", TASK_NAME, "/xml", str(xml_path), "/f"],
        capture_output=True, text=True,
    )
    xml_path.unlink(missing_ok=True)

    if result.returncode == 0:
        print(f"✅ ClipSync installed as a login task.")
        print(f"   User:   {current_user}")
        print(f"   Config: {CONFIG_FILE}")
        print(f"   Logs:   {LOG_FILE}")
        print()
        print(f"   ⚠️  If you ran this as administrator, uninstall and re-run as your normal user:")
        print(f"      python clipsync_tray_win.py --uninstall")
        print(f"      python clipsync_tray_win.py --install   (no admin this time)")
        print()
        print(f"   To stop:      schtasks /end /tn ClipSync")
        print(f"   To start:     schtasks /run /tn ClipSync")
        print(f"   To uninstall: python clipsync_tray_win.py --uninstall")
    else:
        print(f"❌ Task Scheduler registration failed:")
        print(result.stderr)
        print()
        print("If you see 'Access Denied', make sure you are NOT running as administrator.")


def cmd_uninstall():
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("✅ ClipSync login task removed.")
    else:
        print("ℹ️  Task not found — nothing to remove.")


# ── Sync engine (thin wrapper around clipboard_client_v2) ──────────────────────

sys.path.insert(0, str(Path(__file__).parent))

try:
    import clipboard_client_v2 as _client
    CLIENT_AVAILABLE = True
except ImportError as e:
    CLIENT_AVAILABLE = False
    log.error(f"Could not import clipboard_client_v2: {e}")
    log.error("Make sure clipsync_tray_win.py and clipboard_client_v2.py are in the same folder.")


class SyncEngine:
    """Owns the connection loop. Tray calls pause()/resume()/stop()."""

    def __init__(self, host: str):
        self.host      = host
        self._paused   = False
        self._stopped  = False
        self._lock     = threading.Lock()

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def stopped(self) -> bool:
        return self._stopped

    def pause(self):
        with self._lock:
            self._paused = True
        log.info("Sync paused")

    def resume(self):
        with self._lock:
            self._paused = False
        log.info("Sync resumed")

    def stop(self):
        with self._lock:
            self._stopped = True
        log.info("Sync stopping")

    def start(self):
        threading.Thread(target=self._connection_loop, daemon=True).start()

    def _connection_loop(self):
        """Outer reconnect loop — mirrors clipboard_client_v2.main() but respects pause/stop."""
        import socket

        while not self._stopped:
            # Build fresh shared_state for each connection attempt
            initial = _client.read_clipboard()
            shared_state = {
                "last_hash": _client.hash_payload(initial) if initial else "",
                "connected": True,
            }

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect((self.host, _client.PORT))
                sock.settimeout(None)
                log.info(f"Connected to Mac at {self.host}:{_client.PORT}")
            except Exception as e:
                log.error(f"Connection failed: {e} — retrying in {_client.RECONNECT_DELAY}s")
                for _ in range(_client.RECONNECT_DELAY * 2):
                    if self._stopped:
                        return
                    time.sleep(0.5)
                continue

            # Listener thread — receives payloads from Mac
            def listener(sock=sock, ss=shared_state):
                while ss.get("connected") and not self._stopped:
                    payload = _client.decode_message(sock)
                    if payload is None:
                        ss["connected"] = False
                        break
                    if self._paused:
                        continue  # Drop incoming while paused
                    new_hash = _client.apply_incoming(payload, ss)
                    ss["last_hash"] = new_hash

            threading.Thread(target=listener, daemon=True).start()

            # Watcher loop — sends local clipboard changes to Mac
            while shared_state.get("connected") and not self._stopped:
                time.sleep(_client.POLL_INTERVAL)
                if self._paused:
                    continue

                payload = _client.read_clipboard()
                if payload is None:
                    continue

                current_hash = _client.hash_payload(payload)
                if current_hash == shared_state.get("last_hash"):
                    continue

                shared_state["last_hash"] = current_hash
                t = payload["type"]
                if t == "text":
                    log.info(f"→ Text to Mac ({len(payload['text'])} chars)")
                elif t == "image":
                    size = len(payload["png_bytes"])
                    if size > _client.IMAGE_FILE_MAX:
                        log.warning(f"Image too large ({size / 1e6:.1f}MB) — skipping")
                        continue
                    log.info(f"→ Image to Mac ({size / 1e6:.1f}MB)")
                elif t == "file":
                    log.info(f"→ File to Mac: {payload['name']}")

                ok = _client.send_payload(sock, payload)
                if not ok:
                    shared_state["connected"] = False
                    break

            sock.close()
            if not self._stopped:
                log.info(f"Disconnected — reconnecting in {_client.RECONNECT_DELAY}s")
                for _ in range(_client.RECONNECT_DELAY * 2):
                    if self._stopped:
                        return
                    time.sleep(0.5)


# ── Tray icon ──────────────────────────────────────────────────────────────────

def run_tray(engine: SyncEngine):
    try:
        import pystray
    except ImportError:
        log.error("pystray not installed. Run: pip install pystray")
        log.info("Running headless — close this window to quit")
        try:
            while not engine.stopped:
                time.sleep(1)
        except KeyboardInterrupt:
            engine.stop()
        return

    icon_ref: list = [None]

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
        os.startfile(str(CLIPSYNC_DIR))

    def on_quit(icon, item):
        engine.stop()
        icon.stop()

    def make_menu():
        status_text = "⏸ Paused" if engine.paused else "● Active"
        toggle_text = "Resume Sync" if engine.paused else "Pause Sync"
        return pystray.Menu(
            pystray.MenuItem(f"ClipSync — {status_text}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(toggle_text,            on_pause_resume),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open ClipSync Folder", on_open_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit",                 on_quit),
        )

    icon = pystray.Icon(
        name="ClipSync",
        icon=_draw_icon(paused=False),
        title="ClipSync — Active",
        menu=pystray.Menu(lambda: make_menu().items),
    )
    icon_ref[0] = icon

    log.info("Tray icon started — look for the clipboard icon in your system tray")
    icon.run()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ClipSync Tray — Windows")
    parser.add_argument("--host",      help="Local IP of your Mac (saved after first use)")
    parser.add_argument("--install",   action="store_true", help="Register as login task and exit")
    parser.add_argument("--uninstall", action="store_true", help="Remove login task and exit")
    args = parser.parse_args()

    if args.uninstall:
        cmd_uninstall()
        return

    # Resolve host: CLI arg > saved config
    config = load_config()
    host   = args.host or config.get("host")

    if not host:
        parser.error(
            "No Mac IP found. Provide it with --host on first run:\n"
            "  python clipsync_tray_win.py --host 192.168.1.42\n"
            "It will be saved automatically for future runs."
        )

    # Save host for future runs (auto-start won't have --host)
    if args.host and args.host != config.get("host"):
        config["host"] = args.host
        save_config(config)

    if args.install:
        cmd_install(host)
        return

    if not CLIENT_AVAILABLE:
        sys.exit(
            "ERROR: clipboard_client_v2.py not found in the same directory.\n"
            "Place both files in the same folder and try again."
        )

    log.info(f"ClipSync starting — Mac host: {host}")
    log.info(f"ClipSync folder: {CLIPSYNC_DIR}")

    engine = SyncEngine(host)
    engine.start()
    run_tray(engine)


if __name__ == "__main__":
    main()
