"""
clipsync_tray_win.py — System tray for ClipSync V3 (Windows client).

Wraps clipsync_client.ClipSyncClient with a pystray tray icon. Windows
is a client only in V3.

Usage:
    python clipsync_tray_win.py --host <server>           # first run
    python clipsync_tray_win.py                           # subsequent (uses %APPDATA%\\ClipSync\\clipsync.conf)
    python clipsync_tray_win.py --host <server> --install
    python clipsync_tray_win.py --uninstall

--host accepts an IP, a LAN hostname, or a Tailscale MagicDNS name
(e.g. phone.tail-xxxx.ts.net).

V2 collision: --install detects the V2 `ClipSync` scheduled task and
refuses with exact uninstall instructions.

NEVER run --install as administrator. Admin tasks run in a
non-interactive session — no tray, no clipboard. Run as your normal user.

Requirements:
    pip install pystray Pillow pyperclip pywin32

This file must live in the same directory as clipsync_client.py and
clipboard_win.py.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import winreg
from pathlib import Path

from PIL import Image, ImageDraw

# ── Config paths ───────────────────────────────────────────────────────────────
APPDATA       = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
CONFIG_DIR    = APPDATA / "ClipSync"
CONFIG_FILE   = CONFIG_DIR / "clipsync.conf"
LOG_FILE      = CONFIG_DIR / "clipsync_v3.log"
V3_TASK_NAME  = "ClipSyncV3"
V2_TASK_NAME  = "ClipSync"
DEFAULT_PORT  = 9999


def _get_downloads_dir() -> Path:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        ) as k:
            val, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
            return Path(val)
    except OSError:
        return Path.home() / "Downloads"


CLIPSYNC_DIR = _get_downloads_dir() / "ClipSync"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CLIPSYNC_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("clipsync.tray.win")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import clipboard_win as cb
from clipsync_client import ClipSyncClient


# ── Icon ───────────────────────────────────────────────────────────────────────

def _draw_icon(paused: bool = False) -> Image.Image:
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
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


# ── Config ─────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        text = CONFIG_FILE.read_text(encoding="utf-8")
        # V3 writes JSON; V2 wrote key=value. Accept both for transition.
        text = text.strip()
        if text.startswith("{"):
            return json.loads(text)
        cfg: dict = {}
        for line in text.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()
        return cfg
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ── V2 collision detection ─────────────────────────────────────────────────────

def detect_v2() -> bool:
    """True if V2's scheduled task is registered."""
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", V2_TASK_NAME],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def print_v2_uninstall_instructions() -> None:
    print("ERROR: ClipSync V2 is already registered as a scheduled task.")
    print("V3 won't install on top of it — uninstall V2 first, then rerun --install.")
    print()
    print("Run these commands (NOT as administrator):")
    print(f"  schtasks /end /tn {V2_TASK_NAME}")
    print(f"  schtasks /delete /tn {V2_TASK_NAME} /f")
    print()
    print("Or if you still have V2's tray script around:")
    print("  python v2\\clipsync_tray_win.py --uninstall")


# ── Task Scheduler install / uninstall ─────────────────────────────────────────

def cmd_install(host: str, port: int) -> None:
    if detect_v2():
        print_v2_uninstall_instructions()
        sys.exit(2)

    save_config({"host": host, "port": port})

    python_w = Path(sys.executable).with_name(
        Path(sys.executable).name.replace("python.exe", "pythonw.exe")
    )
    python = str(python_w) if python_w.exists() else sys.executable
    script = str(Path(__file__).resolve())
    current_user = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")

    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{current_user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{current_user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
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

    xml_path = CONFIG_DIR / "clipsync_v3_task.xml"
    xml_path.write_text(xml, encoding="utf-16")
    result = subprocess.run(
        ["schtasks", "/create", "/tn", V3_TASK_NAME, "/xml", str(xml_path), "/f"],
        capture_output=True, text=True,
    )
    xml_path.unlink(missing_ok=True)

    if result.returncode == 0:
        print(f"✅ ClipSync V3 installed as a login task ({V3_TASK_NAME}).")
        print(f"   User:   {current_user}")
        print(f"   Config: {CONFIG_FILE}")
        print(f"   Logs:   {LOG_FILE}")
        print(f"   Stop:      schtasks /end /tn {V3_TASK_NAME}")
        print(f"   Start:     schtasks /run /tn {V3_TASK_NAME}")
        print(f"   Uninstall: python clipsync_tray_win.py --uninstall")
        print()
        print(f"   ⚠️  If you ran this as administrator, uninstall and rerun as your normal user.")
    else:
        print(f"❌ Task Scheduler registration failed:")
        print(result.stderr)


def cmd_uninstall() -> None:
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", V3_TASK_NAME, "/f"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("✅ ClipSync V3 login task removed.")
    else:
        print("ℹ️  V3 task not found.")


# ── Tray runtime ───────────────────────────────────────────────────────────────

def run_tray(client: ClipSyncClient) -> None:
    try:
        import pystray
    except ImportError:
        log.error("pystray not installed. Run: pip install pystray")
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
        CLIPSYNC_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(CLIPSYNC_DIR))  # noqa: S606 — intentional, explorer launch

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
    log.info("tray started — check the system tray (^ arrow near clock)")
    icon.run()


# ── Entry ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ClipSync V3 tray (Windows client)")
    parser.add_argument("--host", help="server address (IP, hostname, or Tailscale MagicDNS name)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--install",   action="store_true", help="register login task and exit")
    parser.add_argument("--uninstall", action="store_true", help="remove login task and exit")
    args = parser.parse_args()

    if args.uninstall:
        cmd_uninstall()
        return

    cfg = load_config()
    host = args.host or cfg.get("host")
    port = args.port if args.port != DEFAULT_PORT else int(cfg.get("port", DEFAULT_PORT))

    if args.install:
        if not host:
            sys.exit("--install requires --host on first run")
        cmd_install(host, port)
        return

    if not host:
        sys.exit(f"No --host given and {CONFIG_FILE} missing. Run with --host <server>.")

    save_config({"host": host, "port": port})

    paused = threading.Event()
    stop   = threading.Event()
    client = ClipSyncClient(host, port, cb, paused=paused, stop=stop)
    client.start()
    run_tray(client)


if __name__ == "__main__":
    main()
