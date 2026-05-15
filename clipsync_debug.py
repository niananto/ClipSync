"""
clipsync_debug.py — V3 diagnostic.

Run on Mac or Windows when ClipSync V3 isn't behaving. Cross-platform:
detects what OS it's on and runs only the relevant checks.

    python3 clipsync_debug.py            # Mac
    python clipsync_debug.py             # Windows

Checks:
    1. Python version + executable
    2. Required packages for this platform
    3. Config file present + parseable
    4. V2 collision: is the old V2 launchd agent / scheduled task still
       registered? If yes, V2 and V3 may both be running and racing on
       the clipboard.
    5. V3 install state: is the V3 launchd agent / scheduled task
       registered, and is it currently running?
    6. Server reachability: TCP-connect to the configured host:port.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

OK    = "[OK]   "
WARN  = "[WARN] "
FAIL  = "[FAIL] "
INFO  = "[INFO] "


def section(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def check_python() -> None:
    section("1. Python")
    print(f"{INFO}executable: {sys.executable}")
    print(f"{INFO}version:    {sys.version.split()[0]}")
    if sys.version_info < (3, 10):
        print(f"{FAIL}need Python 3.10+")


def check_packages_mac() -> None:
    section("2. Required packages (Mac)")
    for name, mod in [("pystray", "pystray"), ("Pillow", "PIL"),
                      ("pyobjc-framework-Cocoa", "AppKit")]:
        try:
            __import__(mod)
            print(f"{OK}{name}")
        except ImportError:
            print(f"{FAIL}{name} missing — pip3 install {name}")


def check_packages_win() -> None:
    section("2. Required packages (Windows)")
    for name, mod in [("pystray", "pystray"), ("Pillow", "PIL"),
                      ("pyperclip", "pyperclip"), ("pywin32", "win32clipboard")]:
        try:
            __import__(mod)
            print(f"{OK}{name}")
        except ImportError:
            print(f"{FAIL}{name} missing — pip install {name}")


def check_admin_win() -> None:
    section("2b. Privilege check (Windows)")
    try:
        import ctypes
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        if is_admin:
            print(f"{FAIL}running as ADMINISTRATOR — tray + clipboard will fail.")
            print("       close and re-run as your normal user.")
        else:
            print(f"{OK}running as normal user")
    except OSError as e:
        print(f"{WARN}could not check admin status: {e}")


def load_config_mac() -> dict:
    p = Path.home() / ".clipsync.conf"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_config_win() -> dict:
    p = Path(os.environ.get("APPDATA", "")) / "ClipSync" / "clipsync.conf"
    if not p.exists():
        return {}
    try:
        text = p.read_text(encoding="utf-8").strip()
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


def check_config(cfg: dict) -> dict:
    section("3. Config")
    if not cfg:
        print(f"{WARN}no config file — first run needs --host")
        return cfg
    print(f"{OK}host: {cfg.get('host', '(unset)')}")
    print(f"{OK}port: {cfg.get('port', 9999)}")
    return cfg


def check_collision_mac() -> None:
    section("4. V2 collision (Mac)")
    plist = Path.home() / "Library" / "LaunchAgents" / "com.clipsync.server.plist"
    bad = plist.exists()
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5).stdout
        bad = bad or ("com.clipsync.server" in out and "com.clipsync.v3" not in out.split("com.clipsync.server")[1][:50])
    except (OSError, subprocess.TimeoutExpired):
        pass
    if bad:
        print(f"{FAIL}V2 login agent still installed.")
        print(f"       launchctl unload {plist}")
        print(f"       rm {plist}")
    else:
        print(f"{OK}no V2 agent registered")


def check_v3_state_mac() -> None:
    section("5. V3 install state (Mac)")
    plist = Path.home() / "Library" / "LaunchAgents" / "com.clipsync.v3.client.plist"
    if not plist.exists():
        print(f"{INFO}V3 not installed as login agent (manual-run mode)")
        return
    print(f"{OK}V3 plist: {plist}")
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5).stdout
        if "com.clipsync.v3.client" in out:
            print(f"{OK}V3 agent currently loaded")
        else:
            print(f"{WARN}V3 plist exists but not loaded — try: launchctl load -w {plist}")
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"{WARN}launchctl list failed: {e}")


def check_collision_win() -> None:
    section("4. V2 collision (Windows)")
    try:
        result = subprocess.run(["schtasks", "/query", "/tn", "ClipSync"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print(f"{FAIL}V2 task 'ClipSync' still registered.")
            print(f"       schtasks /end /tn ClipSync")
            print(f"       schtasks /delete /tn ClipSync /f")
        else:
            print(f"{OK}no V2 task registered")
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"{WARN}schtasks check failed: {e}")


def check_v3_state_win() -> None:
    section("5. V3 install state (Windows)")
    try:
        result = subprocess.run(["schtasks", "/query", "/tn", "ClipSyncV3", "/v", "/fo", "LIST"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            print(f"{INFO}V3 task not registered (manual-run mode)")
            return
        print(f"{OK}V3 task 'ClipSyncV3' registered")
        for line in result.stdout.splitlines():
            if "Status:" in line or "Last Run" in line or "Last Result" in line:
                print(f"       {line.strip()}")
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"{WARN}schtasks check failed: {e}")


def check_reachability(cfg: dict) -> None:
    section("6. Server reachability")
    host = cfg.get("host")
    port = int(cfg.get("port", 9999))
    if not host:
        print(f"{WARN}no host configured, skipping")
        return
    print(f"{INFO}trying TCP connect to {host}:{port} (timeout 5s)")
    try:
        with socket.create_connection((host, port), timeout=5):
            print(f"{OK}connected — server is reachable")
    except OSError as e:
        print(f"{FAIL}cannot reach {host}:{port}: {e}")
        print("       check: phone awake, Tailscale up, server running, ACLs")


def main() -> None:
    print("=" * 60)
    print("ClipSync V3 diagnostics")
    print("=" * 60)
    print(f"{INFO}platform: {sys.platform}")

    check_python()

    if sys.platform == "darwin":
        check_packages_mac()
        cfg = check_config(load_config_mac())
        check_collision_mac()
        check_v3_state_mac()
        check_reachability(cfg)
    elif sys.platform == "win32":
        check_packages_win()
        check_admin_win()
        cfg = check_config(load_config_win())
        check_collision_win()
        check_v3_state_win()
        check_reachability(cfg)
    else:
        print(f"{INFO}no platform-specific checks for {sys.platform}")
        print(f"{INFO}for Android: tail -f ~/.clipsync-server-boot.log ~/.clipsync-client-boot.log")

    print()


if __name__ == "__main__":
    main()
