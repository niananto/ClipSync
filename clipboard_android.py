"""
clipboard_android.py — Android (Termux) clipboard adapter for ClipSync V3.

Implements the contract used by clipsync_client.ClipSyncClient. Backed by
the Termux:API command-line tools: `termux-clipboard-get`,
`termux-clipboard-set`, `termux-notification`. Install the API package
and the companion Termux:API app:

    pkg install termux-api      # in Termux
    # plus install "Termux:API" app from F-Droid (NOT Play Store)

Android limits:
    - Android clipboard is text-only. Image and file payloads received from
      peers are saved to CLIPSYNC_DIR; the destination path is placed on
      the clipboard as plain text (so it's still visible/pasteable, just
      not a native file ref like on Mac/Win).
    - Outbound: only text is read from the Android clipboard. Copying a
      file in a file manager won't propagate — there's no portable
      equivalent of CF_HDROP / NSFilenamesPboardType on Android via
      Termux:API. Use a file manager that copies the file path as text if
      you want to share a path.

Run this module inside *native* Termux (not inside proot Debian) so
termux-clipboard-* can reach the host Android clipboard.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger("clipsync.clipboard.android")


def _resolve_clipsync_dir() -> Path:
    """
    Prefer ~/storage/downloads/ClipSync (set up by `termux-setup-storage`)
    so saved files land somewhere visible from the system file manager.
    Fall back to ~/.clipsync/files if storage isn't initialised.
    """
    home = Path(os.environ.get("HOME", "/data/data/com.termux/files/home"))
    storage = home / "storage" / "downloads"
    if storage.exists():
        return storage / "ClipSync"
    return home / ".clipsync" / "files"


CLIPSYNC_DIR = _resolve_clipsync_dir()
CLIPSYNC_DIR.mkdir(parents=True, exist_ok=True)


def _termux_available() -> bool:
    try:
        result = subprocess.run(["which", "termux-clipboard-get"], capture_output=True, text=True, timeout=2)
        return result.returncode == 0 and result.stdout.strip() != ""
    except (OSError, subprocess.TimeoutExpired):
        return False


_TERMUX_OK = _termux_available()
if not _TERMUX_OK:
    log.warning(
        "termux-api tools not found. Install with: pkg install termux-api "
        "and install the Termux:API app from F-Droid."
    )


def read_clipboard() -> dict | None:
    if not _TERMUX_OK:
        return None
    try:
        result = subprocess.run(
            ["termux-clipboard-get"],
            capture_output=True, text=True, encoding="utf-8", timeout=5,
        )
        if result.returncode != 0:
            return None
        text = result.stdout
        # termux-clipboard-get returns the clipboard verbatim. An empty
        # clipboard returns empty string — treat that as None to avoid
        # spamming the wire with empty frames.
        return {"type": "text", "text": text} if text else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def write_clipboard_text(text: str) -> None:
    if not _TERMUX_OK:
        log.warning("termux-api missing — cannot write to clipboard")
        return
    try:
        subprocess.run(
            ["termux-clipboard-set"],
            input=text, text=True, encoding="utf-8",
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning(f"clipboard write failed: {e}")


def write_clipboard_image(png_bytes: bytes) -> None:
    # No image clipboard on Android via Termux:API. The client already
    # saved the file to CLIPSYNC_DIR before calling us (for the spill-over
    # case) but only calls this method for the inline-image branch. Save
    # locally so the user has access to it, and put the path on the
    # clipboard as text.
    dest = CLIPSYNC_DIR / "image.png"
    n = 2
    while dest.exists():
        dest = CLIPSYNC_DIR / f"image ({n}).png"
        n += 1
    dest.write_bytes(png_bytes)
    log.info(f"image saved to {dest}")
    write_clipboard_text(str(dest))


def write_clipboard_fileref(path: Path) -> None:
    write_clipboard_text(str(path))


def notify(title: str, message: str) -> None:
    if not _TERMUX_OK:
        log.info(f"NOTIFY {title}: {message}")
        return
    try:
        subprocess.run(
            ["termux-notification", "--title", title, "--content", message],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        log.info(f"NOTIFY {title}: {message}")
