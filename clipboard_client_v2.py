"""
clipboard_client_v2.py — Run this on your Windows PC
Part of: ClipSync V2 (LAN, text + images + files)

Usage:
    python clipboard_client_v2.py --host <MAC_LOCAL_IP>

    Example:
        python clipboard_client_v2.py --host 192.168.1.42

Requirements:
    pip install pyperclip Pillow pywin32

How content is handled:
    TEXT    → synced directly as clipboard text
    IMAGE   → clipboard image content (e.g. screenshot, copy from browser)
              synced as PNG bytes, placed directly on destination clipboard
              - Under 10MB: sync as clipboard image
              - 10MB–50MB: save to ~/Downloads/ClipSync/, path on clipboard
              - Over 50MB:  skip with warning
    FILE    → file copied in Explorer
              bytes transferred, saved to ~/Downloads/ClipSync/ on Mac
              destination clipboard gets the local path (not re-synced)

V3 TODO: Wrap socket in ssl.SSLContext (load server cert for verification)
V3 TODO: Send HMAC handshake using shared secret immediately after connect
V3 TODO: Load HOST/PORT/SECRET from clipsync.config file
"""

import argparse
import base64
import hashlib
import io
import json
import logging
import os
import socket
import struct
import threading
import time
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

# ── Config ─────────────────────────────────────────────────────────────────────
PORT              = 9999
POLL_INTERVAL     = 0.5
RECONNECT_DELAY   = 5
IMAGE_INLINE_MAX  = 10 * 1024 * 1024   # 10MB
IMAGE_FILE_MAX    = 50 * 1024 * 1024   # 50MB
FILE_MAX          = 50 * 1024 * 1024   # 50MB
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Resolve ClipSync folder (respects custom Downloads location via registry)
def _get_downloads_dir() -> Path:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as key:
            val, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
            return Path(val)
    except Exception:
        return Path.home() / "Downloads"

CLIPSYNC_DIR = _get_downloads_dir() / "ClipSync"
CLIPSYNC_DIR.mkdir(parents=True, exist_ok=True)


# ── Notifications ──────────────────────────────────────────────────────────────

def notify(title: str, message: str) -> None:
    """
    Show a Windows 10/11 toast notification.

    Strategy (tries in order, stops at first success):
      1. plyer — clean cross-platform notification library, most reliable.
         Install once with: pip install plyer
      2. PowerShell BurntToast module — reliable if installed.
         Install once with: Install-Module -Name BurntToast (in PowerShell as admin)
      3. PowerShell msg.exe fallback — shows a simple dialog box, always works,
         but less elegant.

    If all fail, the notification is logged to the terminal instead.
    """
    import subprocess

    safe_title   = title.replace("'", "")
    safe_message = message.replace("'", "")

    # ── Strategy 1: plyer ──
    try:
        from plyer import notification as plyer_notify
        plyer_notify.notify(
            title=title,
            message=message,
            app_name="ClipSync",
            timeout=5,
        )
        return
    except ImportError:
        pass  # plyer not installed — try next
    except Exception:
        pass

    # ── Strategy 2: BurntToast PowerShell module ──
    try:
        ps_script = f"New-BurntToastNotification -Text '{safe_title}', '{safe_message}'"
        result = subprocess.run(
            ["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            return
    except Exception:
        pass

    # ── Strategy 3: msg.exe (always available, shows a dialog box) ──
    try:
        import os
        username = os.environ.get("USERNAME", "*")
        result = subprocess.run(
            ["msg", username, f"{title}: {message}"],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            return
    except Exception:
        pass

    # ── Fallback: terminal log ──
    log.info(f"🔔 {title}: {message}")


# ── Filename conflict resolution ───────────────────────────────────────────────

def resolve_filename(directory: Path, filename: str) -> Path:
    """
    Returns a non-conflicting path in directory.
    photo.png → photo (2).png → photo (3).png ...
    """
    target = directory / filename
    if not target.exists():
        return target
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    n = 2
    while True:
        candidate = directory / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


# ── Clipboard read ─────────────────────────────────────────────────────────────

def read_clipboard() -> dict | None:
    """
    Read Windows clipboard and return a typed payload dict.

    Priority:
      1. File drop (CF_HDROP) — user copied files in Explorer
      2. Image (CF_DIB / ImageGrab) — user copied image content
      3. Text (CF_UNICODETEXT) — everything else
    """
    if not WIN32_AVAILABLE:
        # Fallback: text only
        try:
            text = pyperclip.paste() or ""
            return {"type": "text", "text": text} if text else None
        except Exception:
            return None

    try:
        win32clipboard.OpenClipboard()

        # ── 1. File references ──
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
            try:
                files = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                win32clipboard.CloseClipboard()
                if files:
                    path = Path(files[0])  # First file only
                    if not path.exists():
                        return None
                    # If the file lives in our own ClipSync folder, we put it there —
                    # skip it to prevent the received file echoing back to the sender.
                    try:
                        path.resolve().relative_to(CLIPSYNC_DIR.resolve())
                        return None  # It's ours, don't re-sync
                    except ValueError:
                        pass  # Not in ClipSync folder — genuine user copy, proceed
                    size = path.stat().st_size
                    if size > FILE_MAX:
                        log.warning(f"File too large ({size / 1e6:.1f}MB > 50MB): {path.name}")
                        return None
                    file_bytes = path.read_bytes()
                    return {"type": "file", "name": path.name, "bytes": file_bytes}
            except Exception as e:
                log.warning(f"File clipboard read failed: {e}")
                win32clipboard.CloseClipboard()
                return None

        # ── 2. Image content ──
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB):
            win32clipboard.CloseClipboard()
            try:
                img = ImageGrab.grabclipboard()
                if img is not None:
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    png_bytes = buf.getvalue()
                    return {"type": "image", "png_bytes": png_bytes}
            except Exception as e:
                log.warning(f"Image clipboard read failed: {e}")
            return None

        # ── 3. Plain text ──
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            try:
                text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                win32clipboard.CloseClipboard()
                return {"type": "text", "text": text or ""}
            except Exception as e:
                log.warning(f"Text clipboard read failed: {e}")
                win32clipboard.CloseClipboard()
                return None

        win32clipboard.CloseClipboard()
        return None

    except Exception as e:
        log.warning(f"Clipboard open failed: {e}")
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        return None


# ── Clipboard write ────────────────────────────────────────────────────────────

def write_clipboard_text(text: str) -> None:
    try:
        pyperclip.copy(text)
    except Exception as e:
        log.warning(f"Text clipboard write failed: {e}")


def write_clipboard_image(png_bytes: bytes) -> None:
    """Write PNG bytes to Windows clipboard as a DIB (device-independent bitmap)."""
    if not WIN32_AVAILABLE:
        log.warning("pywin32 not available — cannot write image to clipboard")
        return
    try:
        img = Image.open(io.BytesIO(png_bytes))
        # Convert to BMP in memory — Windows clipboard uses DIB format
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="BMP")
        bmp_data = buf.getvalue()[14:]  # Strip BMP file header, keep DIB

        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, bmp_data)
        win32clipboard.CloseClipboard()
    except Exception as e:
        log.warning(f"Image clipboard write failed: {e}")
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def write_clipboard_fileref(path: Path) -> None:
    """
    Put a native file reference on the Windows clipboard so Ctrl+V works in Explorer.
    Uses CF_HDROP — the same format Explorer writes when you copy a file.
    Falls back to plain text path if pywin32 is unavailable.

    CF_HDROP binary layout:
        DROPFILES struct (20 bytes = 5 x DWORD):
          pFiles  = 20  (DWORD: byte offset to file list from start of struct)
          pt.x    = 0   (DWORD: unused)
          pt.y    = 0   (DWORD: unused)
          fNC     = 0   (DWORD: unused)
          fWide   = 1   (DWORD: 1 = Unicode paths)
        File list: UTF-16LE path string, null-terminated, then extra null to end list
    """
    if not WIN32_AVAILABLE:
        write_clipboard_text(str(path))
        return
    try:
        import struct as _struct
        abs_path = str(path.resolve())
        # UTF-16LE path + single null terminator + list-ending null terminator
        path_bytes = abs_path.encode("utf-16-le") + b"\x00\x00" + b"\x00\x00"
        # Exactly 5 DWORDs = 20 bytes: pFiles=20, pt=(0,0), fNC=0, fWide=1
        header = _struct.pack("<IIIII", 20, 0, 0, 0, 1)
        drop_data = header + path_bytes

        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_HDROP, drop_data)
        win32clipboard.CloseClipboard()
    except Exception as e:
        log.warning(f"Native file ref write failed, falling back to text path: {e}")
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        write_clipboard_text(str(path))


# ── Hashing ────────────────────────────────────────────────────────────────────

def hash_payload(payload: dict) -> str:
    t = payload.get("type")
    if t == "text":
        return hashlib.md5(payload["text"].encode("utf-8")).hexdigest()
    elif t == "image":
        return hashlib.md5(payload["png_bytes"]).hexdigest()
    elif t == "file":
        return hashlib.md5(payload["name"].encode() + payload["bytes"]).hexdigest()
    return ""


# ── Wire framing (identical to server) ────────────────────────────────────────

def encode_message(payload: dict) -> bytes:
    wire = {}
    t = payload.get("type")
    if t == "text":
        wire = {"type": "text", "text": payload["text"]}
    elif t == "image":
        wire = {
            "type": "image",
            "data": base64.b64encode(payload["png_bytes"]).decode("ascii"),
            "size": len(payload["png_bytes"]),
        }
    elif t == "file":
        wire = {
            "type": "file",
            "name": payload["name"],
            "data": base64.b64encode(payload["bytes"]).decode("ascii"),
            "size": len(payload["bytes"]),
        }
    data = json.dumps(wire).encode("utf-8")
    return struct.pack(">I", len(data)) + data


def decode_message(sock: socket.socket) -> dict | None:
    raw_len = _recv_exact(sock, 4)
    if raw_len is None:
        return None
    msg_len = struct.unpack(">I", raw_len)[0]

    if msg_len > 70 * 1024 * 1024:
        log.warning(f"Oversized message ({msg_len / 1e6:.1f}MB) — dropping")
        return None

    raw_data = _recv_exact(sock, msg_len)
    if raw_data is None:
        return None

    try:
        wire = json.loads(raw_data.decode("utf-8"))
    except Exception:
        return None

    t = wire.get("type")
    if t == "text":
        return {"type": "text", "text": wire.get("text", "")}
    elif t == "image":
        return {"type": "image", "png_bytes": base64.b64decode(wire["data"])}
    elif t == "file":
        return {"type": "file", "name": wire["name"], "bytes": base64.b64decode(wire["data"])}
    return None


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        except Exception:
            return None
    return buf


def send_payload(sock: socket.socket, payload: dict) -> bool:
    try:
        sock.sendall(encode_message(payload))
        return True
    except Exception:
        return False


# ── Incoming message handler ───────────────────────────────────────────────────

def apply_incoming(payload: dict, shared_state: dict) -> str:
    """Apply an incoming payload from Mac to Windows. Returns new hash."""
    t = payload.get("type")

    if t == "text":
        text = payload["text"]
        h = hashlib.md5(text.encode()).hexdigest()
        log.info(f"← Text from Mac ({len(text)} chars)")
        write_clipboard_text(text)
        return h

    elif t == "image":
        png_bytes = payload["png_bytes"]
        size = len(png_bytes)
        h = hashlib.md5(png_bytes).hexdigest()

        if size > IMAGE_FILE_MAX:
            log.warning(f"← Image from Mac too large ({size / 1e6:.1f}MB) — skipping")
            return shared_state.get("last_hash", "")

        if size > IMAGE_INLINE_MAX:
            dest = resolve_filename(CLIPSYNC_DIR, "image.png")
            dest.write_bytes(png_bytes)
            write_clipboard_fileref(dest)
            log.info(f"← Large image from Mac saved to {dest}")
            notify("ClipSync", f"Image saved: {dest.name}")
            h = hashlib.md5(str(dest).encode()).hexdigest()
        else:
            write_clipboard_image(png_bytes)
            log.info(f"← Image from Mac ({size / 1e6:.1f}MB) → clipboard")

        return h

    elif t == "file":
        file_bytes = payload["bytes"]
        name = payload["name"]
        dest = resolve_filename(CLIPSYNC_DIR, name)
        dest.write_bytes(file_bytes)
        write_clipboard_fileref(dest)
        log.info(f"← File from Mac: {name} → {dest}")
        notify("ClipSync", f"File received: {dest.name}")
        h = hashlib.md5(str(dest).encode()).hexdigest()
        return h

    return shared_state.get("last_hash", "")


# ── Client threads ─────────────────────────────────────────────────────────────

def listen_for_updates(sock: socket.socket, shared_state: dict) -> None:
    while True:
        payload = decode_message(sock)
        if payload is None:
            log.info("Connection lost — listener exiting")
            shared_state["connected"] = False
            break
        new_hash = apply_incoming(payload, shared_state)
        shared_state["last_hash"] = new_hash


def clipboard_watcher(sock: socket.socket, shared_state: dict) -> None:
    log.info("Clipboard watcher started")
    while shared_state.get("connected"):
        time.sleep(POLL_INTERVAL)
        payload = read_clipboard()
        if payload is None:
            continue

        current_hash = hash_payload(payload)
        if current_hash == shared_state.get("last_hash"):
            continue

        shared_state["last_hash"] = current_hash
        t = payload["type"]

        if t == "text":
            log.info(f"→ Text to Mac ({len(payload['text'])} chars)")
        elif t == "image":
            size = len(payload["png_bytes"])
            if size > IMAGE_FILE_MAX:
                log.warning(f"Clipboard image too large ({size / 1e6:.1f}MB) — skipping")
                continue
            log.info(f"→ Image to Mac ({size / 1e6:.1f}MB)")
        elif t == "file":
            log.info(f"→ File to Mac: {payload['name']} ({len(payload['bytes']) / 1e6:.1f}MB)")

        ok = send_payload(sock, payload)
        if not ok:
            log.warning("Send failed — connection may be lost")
            shared_state["connected"] = False
            break


def connect_and_run(host: str) -> None:
    shared_state = {
        "last_hash": hash_payload(read_clipboard()) if read_clipboard() else "",
        "connected": True,
    }

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, PORT))
        log.info(f"Connected to Mac at {host}:{PORT}")
    except Exception as e:
        log.error(f"Connection failed: {e}")
        return

    listener = threading.Thread(
        target=listen_for_updates, args=(sock, shared_state), daemon=True
    )
    listener.start()

    clipboard_watcher(sock, shared_state)

    sock.close()
    listener.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description="ClipSync V2 — Windows Client")
    parser.add_argument(
        "--host",
        required=True,
        help="Local IP of your Mac (e.g. 192.168.1.42). Find it in System Settings → Network.",
    )
    args = parser.parse_args()

    if not WIN32_AVAILABLE:
        log.warning("pywin32 not found — image/file clipboard support disabled (run: pip install pywin32)")

    log.info(f"ClipSync V2 client starting — target: {args.host}:{PORT}")
    log.info(f"ClipSync folder: {CLIPSYNC_DIR}")
    log.info("Press Ctrl+C to stop")

    while True:
        connect_and_run(args.host)
        log.info(f"Disconnected. Retrying in {RECONNECT_DELAY}s...")
        time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user")
