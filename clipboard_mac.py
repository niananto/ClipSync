"""
clipboard_mac.py — Mac clipboard adapter for ClipSync V3.

Implements the contract used by clipsync_client.ClipSyncClient:
    CLIPSYNC_DIR : Path
    read_clipboard()         -> dict | None
    write_clipboard_text(s)
    write_clipboard_image(png_bytes)
    write_clipboard_fileref(path)
    notify(title, message)

Lifts the Mac-side behaviour from V2's clipboard_server_v2.py: NSPasteboard
for read/write of text, PNG, TIFF, and native file references; Pillow only
used to transcode TIFF→PNG. Falls back to pbcopy/pbpaste if pyobjc is
missing (text-only).
"""

from __future__ import annotations

import io
import logging
import subprocess
from pathlib import Path

from PIL import Image

try:
    from AppKit import (
        NSPasteboard,
        NSPasteboardTypePNG,
        NSPasteboardTypeTIFF,
        NSPasteboardTypeString,
        NSFilenamesPboardType,
    )
    from Foundation import NSData, NSArray
    PYOBJC_AVAILABLE = True
except ImportError:
    PYOBJC_AVAILABLE = False

log = logging.getLogger("clipsync.clipboard.mac")

FILE_MAX = 50 * 1024 * 1024
CLIPSYNC_DIR = Path.home() / "Downloads" / "ClipSync"
CLIPSYNC_DIR.mkdir(parents=True, exist_ok=True)


def read_clipboard() -> dict | None:
    if not PYOBJC_AVAILABLE:
        try:
            result = subprocess.run(["pbpaste"], capture_output=True, text=True, encoding="utf-8")
            text = result.stdout
            return {"type": "text", "text": text} if text else None
        except OSError:
            return None

    pb = NSPasteboard.generalPasteboard()

    file_paths = pb.propertyListForType_(NSFilenamesPboardType)
    if file_paths and len(file_paths) > 0:
        path = Path(str(file_paths[0]))
        if not path.exists():
            return None
        try:
            path.resolve().relative_to(CLIPSYNC_DIR.resolve())
            return None  # ours — would echo
        except ValueError:
            pass
        size = path.stat().st_size
        if size > FILE_MAX:
            log.warning(f"file too large to sync ({size / 1e6:.1f}MB > 50MB): {path.name}")
            return None
        try:
            return {"type": "file", "name": path.name, "bytes": path.read_bytes()}
        except OSError as e:
            log.warning(f"could not read file {path}: {e}")
            return None

    img_data = pb.dataForType_(NSPasteboardTypePNG)
    if img_data is None:
        tiff_data = pb.dataForType_(NSPasteboardTypeTIFF)
        if tiff_data is not None:
            try:
                img = Image.open(io.BytesIO(bytes(tiff_data)))
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return {"type": "image", "png_bytes": buf.getvalue()}
            except (OSError, ValueError) as e:
                log.warning(f"TIFF→PNG conversion failed: {e}")
                return None
    if img_data is not None:
        return {"type": "image", "png_bytes": bytes(img_data)}

    text = pb.stringForType_(NSPasteboardTypeString)
    if text:
        return {"type": "text", "text": str(text)}
    return None


def write_clipboard_text(text: str) -> None:
    if PYOBJC_AVAILABLE:
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)
    else:
        subprocess.run(["pbcopy"], input=text, text=True, encoding="utf-8")


def write_clipboard_image(png_bytes: bytes) -> None:
    if not PYOBJC_AVAILABLE:
        log.warning("pyobjc not available — cannot write image to clipboard")
        return
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    ns_data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
    pb.setData_forType_(ns_data, NSPasteboardTypePNG)


def write_clipboard_fileref(path: Path) -> None:
    if not PYOBJC_AVAILABLE:
        write_clipboard_text(str(path))
        return
    try:
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setPropertyList_forType_(NSArray.arrayWithObject_(str(path)), NSFilenamesPboardType)
    except (OSError, ValueError) as e:
        log.warning(f"file ref write failed, falling back to text path: {e}")
        write_clipboard_text(str(path))


def notify(title: str, message: str) -> None:
    safe_title   = title.replace('"', "'")
    safe_message = message.replace('"', "'")

    try:
        result = subprocess.run(
            ["terminal-notifier", "-title", safe_title, "-message", safe_message,
             "-sender", "com.apple.Terminal"],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            return
    except FileNotFoundError:
        pass
    except (OSError, subprocess.TimeoutExpired):
        pass

    try:
        script = f'display notification "{safe_message}" with title "{safe_title}"'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        if result.returncode == 0:
            return
        log.warning(
            "notification blocked by macOS. fix: System Settings → Notifications → "
            "Terminal → Allow. Or: brew install terminal-notifier"
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning(f"notification failed: {e}")

    log.info(f"NOTIFY {title}: {message}")
