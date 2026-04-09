"""
clipsync_debug.py — Run this to diagnose why ClipSync tray isn't showing.
Place in the same folder as clipsync_tray_win.py and run as your normal user.

    python clipsync_debug.py

It will check every possible failure point and tell you exactly what's wrong.
"""

import sys
import os
import subprocess
import winreg
from pathlib import Path

APPDATA    = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
CONFIG_DIR = APPDATA / "ClipSync"
CONFIG_FILE = CONFIG_DIR / "clipsync.conf"
LOG_FILE   = CONFIG_DIR / "clipsync.log"

print("=" * 60)
print("ClipSync Diagnostics")
print("=" * 60)

# ── 1. Are we running as admin? ───────────────────────────────
print("\n[1] Privilege check")
try:
    import ctypes
    is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    if is_admin:
        print("  ❌ Running as ADMINISTRATOR — this will break the tray icon.")
        print("     Close this window and re-run as your normal user (no 'Run as admin').")
    else:
        print("  ✅ Running as normal user (correct)")
except Exception as e:
    print(f"  ⚠️  Could not check admin status: {e}")

# ── 2. Python executable ──────────────────────────────────────
print("\n[2] Python")
print(f"  Executable: {sys.executable}")
print(f"  Version:    {sys.version}")

# ── 3. Required packages ──────────────────────────────────────
print("\n[3] Required packages")
packages = ["pystray", "PIL", "pyperclip", "win32clipboard"]
for pkg in packages:
    try:
        __import__(pkg)
        print(f"  ✅ {pkg}")
    except ImportError:
        pip_name = {"PIL": "Pillow", "win32clipboard": "pywin32"}.get(pkg, pkg)
        print(f"  ❌ {pkg} — install with: pip install {pip_name}")

# ── 4. ClipSync files present ────────────────────────────────
print("\n[4] ClipSync files")
here = Path(__file__).parent
for fname in ["clipboard_client_v2.py", "clipsync_tray_win.py"]:
    p = here / fname
    print(f"  {'✅' if p.exists() else '❌'} {fname} {'(found)' if p.exists() else '(MISSING)'}")

# ── 5. Config file ────────────────────────────────────────────
print("\n[5] Config file")
if CONFIG_FILE.exists():
    content = CONFIG_FILE.read_text()
    print(f"  ✅ Found at {CONFIG_FILE}")
    print(f"     Contents: {content.strip()}")
    if "host=" not in content:
        print("  ❌ No 'host' key found in config — run with --host 192.168.10.145")
else:
    print(f"  ❌ Not found at {CONFIG_FILE}")
    print(f"     Run: python clipsync_tray_win.py --host 192.168.10.145")

# ── 6. Task Scheduler task ────────────────────────────────────
print("\n[6] Task Scheduler task")
result = subprocess.run(
    ["schtasks", "/query", "/tn", "ClipSync", "/fo", "LIST", "/v"],
    capture_output=True, text=True,
)
if result.returncode == 0:
    lines = {
        line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip()
        for line in result.stdout.splitlines()
        if ":" in line
    }
    print(f"  ✅ Task exists")
    run_as = lines.get("Run As User", "unknown")
    logon  = lines.get("Logon Mode",  "unknown")
    status = lines.get("Status",      "unknown")
    print(f"     Run As User: {run_as}")
    print(f"     Logon Mode:  {logon}")
    print(f"     Status:      {status}")
    if "interactive" not in logon.lower():
        print("  ❌ Logon Mode is NOT interactive — tray will NOT appear.")
        print("     Fix: python clipsync_tray_win.py --uninstall")
        print("          python clipsync_tray_win.py --install  (as normal user, no admin)")
    else:
        print("  ✅ Logon mode is interactive (correct)")
    if "SYSTEM" in run_as.upper() or "Administrator" in run_as:
        print(f"  ❌ Task is registered under '{run_as}' — must be your own user account.")
else:
    print("  ℹ️  No ClipSync task found (not installed yet)")

# ── 7. Log file ───────────────────────────────────────────────
print("\n[7] Log file")
if LOG_FILE.exists():
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    last  = lines[-20:] if len(lines) > 20 else lines
    print(f"  ✅ Found at {LOG_FILE}")
    print(f"  Last {len(last)} lines:")
    for line in last:
        print(f"    {line}")
else:
    print(f"  ℹ️  No log file yet at {LOG_FILE}")
    print(f"     The task hasn't run successfully yet, or it crashed before logging.")

# ── 8. Try importing tray script directly ────────────────────
print("\n[8] Import test")
try:
    sys.path.insert(0, str(here))
    import clipsync_tray_win
    print("  ✅ clipsync_tray_win.py imports without errors")
except Exception as e:
    print(f"  ❌ Import failed: {e}")

# ── 9. pystray smoke test ─────────────────────────────────────
print("\n[9] pystray smoke test")
try:
    import pystray
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (30, 140, 120, 220))
    icon = pystray.Icon("test", img, "ClipSync Test")
    print("  ✅ pystray icon object created successfully")
    print("  ℹ️  To fully test the tray, run: python clipsync_tray_win.py")
    print("     (the smoke test can't display the icon without blocking)")
except Exception as e:
    print(f"  ❌ pystray failed: {e}")

# ── Summary ───────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Next steps:")
print("  1. Fix any ❌ items above")
print("  2. Run manually first to confirm tray works:")
print("     python clipsync_tray_win.py")
print("  3. If manual works but auto-start doesn't, the Task Scheduler")
print("     logon mode is wrong — uninstall and reinstall as normal user.")
print("=" * 60)
