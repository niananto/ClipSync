"""
clipboard_server_v2.py — Run this on your Mac
Part of: ClipSync V2 (LAN, text + images + files)

Usage:
    python3 clipboard_server_v2.py

Requirements:
    pip3 install pyobjc-framework-Cocoa Pillow

    pyobjc gives us full NSPasteboard access (images, file references, text).
    Pillow is used to encode/decode clipboard images to/from PNG bytes.

How content is handled:
    TEXT    → synced directly as clipboard text (same as V1)
    IMAGE   → clipboard image content (e.g. screenshot, copy from browser)
              synced as PNG bytes, placed directly on destination clipboard
              - Under 10MB: sync as clipboard image
              - 10MB–50MB: save to ~/Downloads/ClipSync/, path on clipboard
              - Over 50MB:  skip with warning
    FILE    → file copied in Finder/Explorer
              bytes transferred, saved to ~/Downloads/ClipSync/ on destination
              filename preserved; conflicts resolved as photo (2).png etc.
              destination clipboard gets the local path (not re-synced)

Message frame format (unchanged from V1, size cap raised):
    [4-byte big-endian length][JSON bytes]

    JSON payload shapes:
        { "type": "text",  "text": "..." }
        { "type": "image", "data": "<base64 PNG>", "size": <bytes> }
        { "type": "file",  "name": "photo.png", "data": "<base64 bytes>", "size": <bytes> }

V3 TODO: Wrap socket in ssl.SSLContext (self-signed cert, server-side)
V3 TODO: Add HMAC handshake using shared secret before any clipboard data flows
V3 TODO: Load HOST/PORT/SECRET from clipsync.config file
"""

import base64
import hashlib
import json
import logging
import os
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image
import io

try:
    from AppKit import NSPasteboard, NSPasteboardTypePNG, NSPasteboardTypeTIFF
    from AppKit import NSPasteboardTypeString, NSFilenamesPboardType
    from Foundation import NSData, NSArray
    PYOBJC_AVAILABLE = True
except ImportError:
    PYOBJC_AVAILABLE = False

# ── Config ─────────────────────────────────────────────────────────────────────
HOST            = "0.0.0.0"
PORT            = 9999
POLL_INTERVAL   = 0.5           # Seconds between clipboard polls
CLIPSYNC_DIR    = Path.home() / "Downloads" / "ClipSync"
IMAGE_INLINE_MAX  = 10 * 1024 * 1024   # 10MB  — sync as clipboard image
IMAGE_FILE_MAX    = 50 * 1024 * 1024   # 50MB  — save to ClipSync folder
FILE_MAX          = 50 * 1024 * 1024   # 50MB  — hard cap for file transfers
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

CLIPSYNC_DIR.mkdir(parents=True, exist_ok=True)


# ── Notifications ──────────────────────────────────────────────────────────────

def notify(title: str, message: str) -> None:
    """
    Show a macOS notification.

    Strategy (tries in order, stops at first success):
      1. terminal-notifier CLI — most reliable, respects Do Not Disturb,
         shows the app icon. Install once with: brew install terminal-notifier
      2. osascript — built-in, but requires notification permission for
         Terminal (or whatever app is running this script).
         Grant it in: System Settings → Notifications → Terminal → Allow

    If neither works, the notification is logged to the terminal instead
    so you never silently miss a file arrival.
    """
    # Sanitise to avoid breaking shell quoting
    safe_title   = title.replace('"', "'")
    safe_message = message.replace('"', "'")

    # ── Strategy 1: terminal-notifier ──
    try:
        result = subprocess.run(
            ["terminal-notifier", "-title", safe_title, "-message", safe_message,
             "-sender", "com.apple.Terminal"],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            return
    except FileNotFoundError:
        pass  # terminal-notifier not installed — try osascript
    except Exception:
        pass

    # ── Strategy 2: osascript ──
    try:
        script = f'display notification "{safe_message}" with title "{safe_title}"'
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            return
        else:
            # osascript ran but notification was blocked
            log.warning(
                "Notification blocked by macOS. To fix: System Settings → "
                "Notifications → Terminal → set to 'Alerts' or 'Banners'. "
                "Or install terminal-notifier: brew install terminal-notifier"
            )
    except Exception as e:
        log.warning(f"Notification failed: {e}")

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
    Read the current Mac clipboard and return a typed payload dict, or None on error.

    Priority order:
      1. File references (NSFilenamesPboardType) — user copied a file in Finder
      2. Image content (PNG or TIFF)             — user copied image data
      3. Plain text                               — everything else

    Returns one of:
      { "type": "text",  "text": str }
      { "type": "image", "png_bytes": bytes }
      { "type": "file",  "name": str, "bytes": bytes }
      None on empty or unreadable clipboard
    """
    if not PYOBJC_AVAILABLE:
        # Fallback to pbpaste for text only (pyobjc not installed)
        try:
            result = subprocess.run(["pbpaste"], capture_output=True, text=True, encoding="utf-8")
            text = result.stdout
            return {"type": "text", "text": text} if text else None
        except Exception:
            return None

    pb = NSPasteboard.generalPasteboard()

    # ── 1. File references ──
    file_paths = pb.propertyListForType_(NSFilenamesPboardType)
    if file_paths and len(file_paths) > 0:
        path = Path(str(file_paths[0]))  # Only sync the first file for now
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
            log.warning(f"File too large to sync ({size / 1e6:.1f}MB > 50MB): {path.name}")
            return None
        try:
            file_bytes = path.read_bytes()
            return {"type": "file", "name": path.name, "bytes": file_bytes}
        except Exception as e:
            log.warning(f"Could not read file {path}: {e}")
            return None

    # ── 2. Image content ──
    # Try PNG first, fall back to TIFF (which we convert to PNG)
    img_data = pb.dataForType_(NSPasteboardTypePNG)
    if img_data is None:
        img_data = pb.dataForType_(NSPasteboardTypeTIFF)
        if img_data is not None:
            # Convert TIFF → PNG via Pillow
            try:
                tiff_bytes = bytes(img_data)
                img = Image.open(io.BytesIO(tiff_bytes))
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                png_bytes = buf.getvalue()
                return {"type": "image", "png_bytes": png_bytes}
            except Exception as e:
                log.warning(f"TIFF→PNG conversion failed: {e}")
                return None
    if img_data is not None:
        return {"type": "image", "png_bytes": bytes(img_data)}

    # ── 3. Plain text ──
    text = pb.stringForType_(NSPasteboardTypeString)
    if text:
        return {"type": "text", "text": str(text)}

    return None


# ── Clipboard write ────────────────────────────────────────────────────────────

def write_clipboard_text(text: str) -> None:
    """Write plain text to Mac clipboard."""
    if PYOBJC_AVAILABLE:
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)
    else:
        subprocess.run(["pbcopy"], input=text, text=True, encoding="utf-8")


def write_clipboard_image(png_bytes: bytes) -> None:
    """Write PNG image bytes directly to Mac clipboard."""
    if not PYOBJC_AVAILABLE:
        log.warning("pyobjc not available — cannot write image to clipboard")
        return
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    ns_data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
    pb.setData_forType_(ns_data, NSPasteboardTypePNG)


def write_clipboard_fileref(path: Path) -> None:
    """
    Put a native file reference on the Mac clipboard so Cmd+V works in Finder.
    Uses NSFilenamesPboardType — the same format Finder writes when you copy a file.
    Falls back to plain text path if pyobjc is unavailable.
    """
    if not PYOBJC_AVAILABLE:
        write_clipboard_text(str(path))
        return
    try:
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        # NSFilenamesPboardType expects an NSArray of absolute path strings
        pb.setPropertyList_forType_(NSArray.arrayWithObject_(str(path)), NSFilenamesPboardType)
    except Exception as e:
        log.warning(f"Native file ref write failed, falling back to text path: {e}")
        write_clipboard_text(str(path))


# ── Hashing ────────────────────────────────────────────────────────────────────

def hash_payload(payload: dict) -> str:
    """Produce a stable hash from any payload type for change detection."""
    t = payload.get("type")
    if t == "text":
        return hashlib.md5(payload["text"].encode("utf-8")).hexdigest()
    elif t == "image":
        return hashlib.md5(payload["png_bytes"]).hexdigest()
    elif t == "file":
        return hashlib.md5(payload["name"].encode() + payload["bytes"]).hexdigest()
    return ""


# ── Wire framing ───────────────────────────────────────────────────────────────

def encode_message(payload: dict) -> bytes:
    """
    Serialize payload to wire format: [4-byte length][JSON].
    Binary fields (png_bytes, file bytes) are base64-encoded in the JSON.
    """
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
    """
    Read one length-prefixed message from socket and decode it.
    Returns internal payload dict (with bytes fields decoded from base64).
    """
    raw_len = _recv_exact(sock, 4)
    if raw_len is None:
        return None
    msg_len = struct.unpack(">I", raw_len)[0]

    # 50MB wire cap (base64 overhead ~33%, so ~67MB JSON for 50MB binary)
    if msg_len > 70 * 1024 * 1024:
        log.warning(f"Oversized message ({msg_len / 1e6:.1f}MB) — dropping")
        return None

    raw_data = _recv_exact(sock, msg_len)
    if raw_data is None:
        return None

    try:
        wire = json.loads(raw_data.decode("utf-8"))
    except Exception:
        log.warning("JSON decode failed")
        return None

    t = wire.get("type")
    if t == "text":
        return {"type": "text", "text": wire.get("text", "")}
    elif t == "image":
        png_bytes = base64.b64decode(wire["data"])
        return {"type": "image", "png_bytes": png_bytes}
    elif t == "file":
        file_bytes = base64.b64decode(wire["data"])
        return {"type": "file", "name": wire["name"], "bytes": file_bytes}

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
    """
    Apply an incoming payload from Windows to the Mac.
    Updates shared_state["last_hash"] to prevent echo loops.
    Returns the new hash.
    """
    t = payload.get("type")

    if t == "text":
        text = payload["text"]
        h = hashlib.md5(text.encode()).hexdigest()
        log.info(f"← Text from Windows ({len(text)} chars)")
        write_clipboard_text(text)
        return h

    elif t == "image":
        png_bytes = payload["png_bytes"]
        size = len(png_bytes)
        h = hashlib.md5(png_bytes).hexdigest()

        if size > IMAGE_FILE_MAX:
            log.warning(f"← Image from Windows too large ({size / 1e6:.1f}MB) — skipping")
            return shared_state.get("last_hash", "")

        if size > IMAGE_INLINE_MAX:
            # Save to ClipSync folder, put path on clipboard
            dest = resolve_filename(CLIPSYNC_DIR, "image.png")
            dest.write_bytes(png_bytes)
            write_clipboard_fileref(dest)
            log.info(f"← Large image from Windows saved to {dest}")
            notify("ClipSync", f"Image saved: {dest.name}")
            # Hash the path string so it doesn't re-sync
            h = hashlib.md5(str(dest).encode()).hexdigest()
        else:
            write_clipboard_image(png_bytes)
            log.info(f"← Image from Windows ({size / 1e6:.1f}MB) → clipboard")

        return h

    elif t == "file":
        file_bytes = payload["bytes"]
        name = payload["name"]
        h = hashlib.md5(name.encode() + file_bytes).hexdigest()

        dest = resolve_filename(CLIPSYNC_DIR, name)
        dest.write_bytes(file_bytes)
        write_clipboard_fileref(dest)
        log.info(f"← File from Windows: {name} → {dest}")
        notify("ClipSync", f"File received: {dest.name}")
        # Hash the path so it doesn't re-sync as a "new clipboard text"
        h = hashlib.md5(str(dest).encode()).hexdigest()
        return h

    return shared_state.get("last_hash", "")


# ── Server threads ─────────────────────────────────────────────────────────────

def handle_client(conn: socket.socket, addr, shared_state: dict) -> None:
    log.info(f"Windows client connected: {addr}")
    shared_state["conn"] = conn
    try:
        while True:
            payload = decode_message(conn)
            if payload is None:
                log.info(f"Client disconnected: {addr}")
                break
            new_hash = apply_incoming(payload, shared_state)
            shared_state["last_hash"] = new_hash
    except Exception as e:
        log.error(f"Client handler error: {e}")
    finally:
        shared_state["conn"] = None
        conn.close()


def clipboard_watcher(shared_state: dict) -> None:
    """Poll Mac clipboard; push changes to Windows when connected."""
    log.info("Clipboard watcher started")
    while True:
        time.sleep(POLL_INTERVAL)
        payload = read_clipboard()
        if payload is None:
            continue

        current_hash = hash_payload(payload)
        if current_hash == shared_state.get("last_hash"):
            continue

        shared_state["last_hash"] = current_hash
        conn = shared_state.get("conn")
        if not conn:
            continue

        t = payload["type"]
        if t == "text":
            log.info(f"→ Text to Windows ({len(payload['text'])} chars)")
        elif t == "image":
            size = len(payload["png_bytes"])
            if size > IMAGE_FILE_MAX:
                log.warning(f"Clipboard image too large ({size / 1e6:.1f}MB) — skipping")
                continue
            log.info(f"→ Image to Windows ({size / 1e6:.1f}MB)")
        elif t == "file":
            log.info(f"→ File to Windows: {payload['name']} ({len(payload['bytes']) / 1e6:.1f}MB)")

        ok = send_payload(conn, payload)
        if not ok:
            log.warning("Send failed — client may have disconnected")


def main():
    if not PYOBJC_AVAILABLE:
        log.warning("pyobjc not found — falling back to text-only mode (run: pip3 install pyobjc-framework-Cocoa)")

    # Seed state with current clipboard so we don't blast it on startup
    initial = read_clipboard()
    shared_state = {
        "last_hash": hash_payload(initial) if initial else "",
        "conn": None,
    }

    watcher = threading.Thread(target=clipboard_watcher, args=(shared_state,), daemon=True)
    watcher.start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(1)
    log.info(f"ClipSync V2 server listening on port {PORT}")
    log.info(f"ClipSync folder: {CLIPSYNC_DIR}")
    log.info("Waiting for Windows client...")

    while True:
        conn, addr = server.accept()
        t = threading.Thread(target=handle_client, args=(conn, addr, shared_state), daemon=True)
        t.start()


if __name__ == "__main__":
    main()
