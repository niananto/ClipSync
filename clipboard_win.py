"""
clipboard_win.py — Windows clipboard adapter for ClipSync V3.

Implements the contract used by clipsync_client.ClipSyncClient:
    CLIPSYNC_DIR : Path
    read_clipboard()         -> dict | None
    write_clipboard_text(s)
    write_clipboard_image(png_bytes)
    write_clipboard_fileref(path)
    notify(title, message)

Lifts the Windows-side behaviour from V2's clipboard_client_v2.py:
pyperclip for text, win32clipboard + CF_HDROP for native file refs,
PIL.ImageGrab for clipboard images. CLIPSYNC_DIR honours Windows' custom
Downloads location via the registry shell-folder key.
"""

from __future__ import annotations

import io
import logging
import os
import struct
import subprocess
import winreg
from pathlib import Path

import pyperclip
from PIL import Image, ImageGrab

try:
    import win32clipboard
    import win32con
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

log = logging.getLogger("clipsync.clipboard.win")

FILE_MAX = 50 * 1024 * 1024


def _get_downloads_dir() -> Path:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
            return Path(val)
    except OSError:
        return Path.home() / "Downloads"


CLIPSYNC_DIR = _get_downloads_dir() / "ClipSync"
CLIPSYNC_DIR.mkdir(parents=True, exist_ok=True)


def read_clipboard() -> dict | None:
    if not WIN32_AVAILABLE:
        try:
            text = pyperclip.paste() or ""
            return {"type": "text", "text": text} if text else None
        except pyperclip.PyperclipException:
            return None

    try:
        win32clipboard.OpenClipboard()

        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
            try:
                files = win32clipboard.GetClipboardData(win32con.CF_HDROP)
            finally:
                win32clipboard.CloseClipboard()
            if not files:
                return None
            path = Path(files[0])
            if not path.exists():
                return None
            try:
                path.resolve().relative_to(CLIPSYNC_DIR.resolve())
                return None  # ours — would echo
            except ValueError:
                pass
            size = path.stat().st_size
            if size > FILE_MAX:
                log.warning(f"file too large ({size / 1e6:.1f}MB > 50MB): {path.name}")
                return None
            return {"type": "file", "name": path.name, "bytes": path.read_bytes()}

        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB):
            win32clipboard.CloseClipboard()
            img = ImageGrab.grabclipboard()
            if img is None:
                return None
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return {"type": "image", "png_bytes": buf.getvalue()}

        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            try:
                text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            return {"type": "text", "text": text or ""} if text else None

        win32clipboard.CloseClipboard()
        return None

    except OSError as e:
        log.warning(f"clipboard open failed: {e}")
        try:
            win32clipboard.CloseClipboard()
        except OSError:
            pass
        return None


def write_clipboard_text(text: str) -> None:
    try:
        pyperclip.copy(text)
    except pyperclip.PyperclipException as e:
        log.warning(f"text clipboard write failed: {e}")


def write_clipboard_image(png_bytes: bytes) -> None:
    if not WIN32_AVAILABLE:
        log.warning("pywin32 not available — cannot write image to clipboard")
        return
    try:
        img = Image.open(io.BytesIO(png_bytes))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="BMP")
        bmp_data = buf.getvalue()[14:]  # strip BMP file header, keep DIB

        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, bmp_data)
        win32clipboard.CloseClipboard()
    except (OSError, ValueError) as e:
        log.warning(f"image clipboard write failed: {e}")
        try:
            win32clipboard.CloseClipboard()
        except OSError:
            pass


def write_clipboard_fileref(path: Path) -> None:
    """
    Native CF_HDROP write so Ctrl+V in Explorer pastes the file.
    DROPFILES layout: 5 DWORDs (pFiles=20, pt.x=0, pt.y=0, fNC=0, fWide=1),
    then a UTF-16LE null-terminated path, then an extra null terminator
    to end the file list.
    """
    if not WIN32_AVAILABLE:
        write_clipboard_text(str(path))
        return
    try:
        abs_path = str(path.resolve())
        path_bytes = abs_path.encode("utf-16-le") + b"\x00\x00" + b"\x00\x00"
        header = struct.pack("<IIIII", 20, 0, 0, 0, 1)
        drop_data = header + path_bytes

        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_HDROP, drop_data)
        win32clipboard.CloseClipboard()
    except OSError as e:
        log.warning(f"file ref write failed, falling back to text path: {e}")
        try:
            win32clipboard.CloseClipboard()
        except OSError:
            pass
        write_clipboard_text(str(path))


def notify(title: str, message: str) -> None:
    safe_title   = title.replace("'", "")
    safe_message = message.replace("'", "")

    try:
        from plyer import notification as plyer_notify
        plyer_notify.notify(title=title, message=message, app_name="ClipSync", timeout=5)
        return
    except ImportError:
        pass
    except Exception:  # plyer wraps a pile of platform errors
        pass

    try:
        ps_script = f"New-BurntToastNotification -Text '{safe_title}', '{safe_message}'"
        result = subprocess.run(
            ["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            return
    except (OSError, subprocess.TimeoutExpired):
        pass

    try:
        username = os.environ.get("USERNAME", "*")
        result = subprocess.run(
            ["msg", username, f"{title}: {message}"],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            return
    except (OSError, subprocess.TimeoutExpired):
        pass

    log.info(f"NOTIFY {title}: {message}")
