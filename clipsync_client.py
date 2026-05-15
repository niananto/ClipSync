"""
clipsync_client.py — Shared client core for ClipSync V3.

Platform-agnostic transport, framing, pause/stop coordination, and
clipboard-watcher loop. The platform-specific bits (reading and writing
the OS clipboard, sending notifications, choosing where saved files
land) are delegated to a clipboard module passed in at construction.

Run directly to pick the platform module automatically:
    python3 clipsync_client.py --host phone.tail-xxxx.ts.net

Or import and embed (used by the tray wrappers):
    from clipsync_client import ClipSyncClient
    import clipboard_mac as cb
    client = ClipSyncClient(host, port, cb)
    client.start()

Wire format (compatible with V2):
    [4-byte big-endian length][JSON UTF-8]
    text:  { "type": "text",  "text": "..." }
    image: { "type": "image", "data": "<base64 PNG>",   "size": N }
    file:  { "type": "file",  "name": "x.png", "data": "<base64>", "size": N }

Echo prevention:
    Each side keeps last_hash. After a send OR receive, last_hash is set to
    the content hash so the next poll won't re-emit. Saved files (image
    spillover, file payloads) hash the destination *path* (not the bytes)
    so the file-ref placed on the clipboard doesn't loop. The relay server
    additionally never sends a frame back to its originating client.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Protocol

# ── Config ─────────────────────────────────────────────────────────────────────
DEFAULT_PORT      = 9999
POLL_INTERVAL     = 0.5             # seconds between clipboard polls
RECONNECT_DELAY   = 5               # seconds between reconnect attempts
SOCKET_TIMEOUT    = None            # blocking reads on the receive thread
IMAGE_INLINE_MAX  = 10 * 1024 * 1024
IMAGE_FILE_MAX    = 50 * 1024 * 1024
FILE_MAX          = 50 * 1024 * 1024
MAX_FRAME_BYTES   = 70 * 1024 * 1024
# ───────────────────────────────────────────────────────────────────────────────

log = logging.getLogger("clipsync.client")


# ── Clipboard module contract ──────────────────────────────────────────────────

class ClipboardModule(Protocol):
    """Each platform module (clipboard_mac/win/android) must satisfy this."""

    CLIPSYNC_DIR: Path

    def read_clipboard(self) -> dict | None: ...
    def write_clipboard_text(self, text: str) -> None: ...
    def write_clipboard_image(self, png_bytes: bytes) -> None: ...
    def write_clipboard_fileref(self, path: Path) -> None: ...
    def notify(self, title: str, message: str) -> None: ...


# ── Framing ────────────────────────────────────────────────────────────────────

def encode_message(payload: dict) -> bytes:
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
    else:
        raise ValueError(f"unknown payload type: {t}")
    body = json.dumps(wire).encode("utf-8")
    return struct.pack(">I", len(body)) + body


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def decode_message(sock: socket.socket) -> dict | None:
    header = _recv_exact(sock, 4)
    if header is None:
        return None
    (msg_len,) = struct.unpack(">I", header)
    if msg_len == 0 or msg_len > MAX_FRAME_BYTES:
        log.warning(f"oversize frame ({msg_len}B) — dropping")
        return None
    raw = _recv_exact(sock, msg_len)
    if raw is None:
        return None
    try:
        wire = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        log.warning("frame JSON decode failed")
        return None
    t = wire.get("type")
    if t == "text":
        return {"type": "text", "text": wire.get("text", "")}
    if t == "image":
        return {"type": "image", "png_bytes": base64.b64decode(wire["data"])}
    if t == "file":
        return {"type": "file", "name": wire["name"], "bytes": base64.b64decode(wire["data"])}
    return None


# ── Hashing ────────────────────────────────────────────────────────────────────

def hash_payload(payload: dict) -> str:
    t = payload.get("type")
    if t == "text":
        return hashlib.md5(payload["text"].encode("utf-8")).hexdigest()
    if t == "image":
        return hashlib.md5(payload["png_bytes"]).hexdigest()
    if t == "file":
        return hashlib.md5(payload["name"].encode("utf-8") + payload["bytes"]).hexdigest()
    return ""


# ── Filename conflict resolution ───────────────────────────────────────────────

def resolve_filename(directory: Path, filename: str) -> Path:
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


# ── Client ─────────────────────────────────────────────────────────────────────

class ClipSyncClient:
    """
    Threaded TCP client: one watcher thread polling the local clipboard,
    one receive thread reading server frames. Reconnects on drop.

    The tray wrappers construct one of these, hold the paused/stop Events,
    and toggle them from menu callbacks. Standalone CLI mode just lets
    both threads run until SIGINT.
    """

    def __init__(
        self,
        host: str,
        port: int,
        clipboard: ClipboardModule,
        paused: threading.Event | None = None,
        stop: threading.Event | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.cb = clipboard
        self.paused = paused or threading.Event()
        self.stop   = stop   or threading.Event()
        self._sock: socket.socket | None = None
        self._sock_lock = threading.Lock()
        self._last_hash = ""
        self._watcher: threading.Thread | None = None
        self._receiver: threading.Thread | None = None

    # ── Public API ──
    def start(self) -> None:
        # Seed last_hash with current clipboard so we don't blast it on connect.
        initial = self.cb.read_clipboard()
        if initial is not None:
            self._last_hash = hash_payload(initial)

        self._watcher = threading.Thread(target=self._watch_clipboard, daemon=True, name="cs-watch")
        self._receiver = threading.Thread(target=self._connect_loop,   daemon=True, name="cs-net")
        self._watcher.start()
        self._receiver.start()

    def wait(self) -> None:
        try:
            while not self.stop.is_set():
                self.stop.wait(timeout=1.0)
        except KeyboardInterrupt:
            self.stop.set()

    def shutdown(self) -> None:
        self.stop.set()
        with self._sock_lock:
            if self._sock is not None:
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    # ── Network loop ──
    def _connect_loop(self) -> None:
        while not self.stop.is_set():
            try:
                log.info(f"connecting to {self.host}:{self.port}")
                sock = socket.create_connection((self.host, self.port), timeout=10)
                sock.settimeout(SOCKET_TIMEOUT)
                with self._sock_lock:
                    self._sock = sock
                log.info(f"connected to {self.host}:{self.port}")
                self._recv_until_closed(sock)
            except OSError as e:
                log.warning(f"connection failed: {e}")
            finally:
                with self._sock_lock:
                    if self._sock is not None:
                        try:
                            self._sock.close()
                        except OSError:
                            pass
                        self._sock = None
            if self.stop.is_set():
                break
            log.info(f"reconnecting in {RECONNECT_DELAY}s")
            self.stop.wait(timeout=RECONNECT_DELAY)

    def _recv_until_closed(self, sock: socket.socket) -> None:
        while not self.stop.is_set():
            payload = decode_message(sock)
            if payload is None:
                log.info("server closed connection")
                return
            if self.paused.is_set():
                log.info("paused — dropping incoming frame")
                continue
            self._apply_incoming(payload)

    # ── Clipboard polling ──
    def _watch_clipboard(self) -> None:
        while not self.stop.is_set():
            self.stop.wait(timeout=POLL_INTERVAL)
            if self.stop.is_set():
                return
            if self.paused.is_set():
                continue
            payload = self.cb.read_clipboard()
            if payload is None:
                continue

            # Drop outgoing payloads that exceed caps (don't even hash huge stuff).
            t = payload["type"]
            if t == "image" and len(payload["png_bytes"]) > IMAGE_FILE_MAX:
                log.warning(f"clipboard image too large ({len(payload['png_bytes']) / 1e6:.1f}MB) — skipping")
                continue
            if t == "file" and len(payload["bytes"]) > FILE_MAX:
                log.warning(f"clipboard file too large ({len(payload['bytes']) / 1e6:.1f}MB) — skipping")
                continue

            h = hash_payload(payload)
            if h == self._last_hash:
                continue
            self._last_hash = h
            self._send(payload)

    def _send(self, payload: dict) -> None:
        with self._sock_lock:
            sock = self._sock
        if sock is None:
            return
        try:
            frame = encode_message(payload)
        except (ValueError, TypeError) as e:
            log.warning(f"encode failed: {e}")
            return
        t = payload["type"]
        if t == "text":
            log.info(f"→ text ({len(payload['text'])} chars)")
        elif t == "image":
            log.info(f"→ image ({len(payload['png_bytes']) / 1e6:.2f}MB)")
        elif t == "file":
            log.info(f"→ file {payload['name']} ({len(payload['bytes']) / 1e6:.2f}MB)")
        try:
            sock.sendall(frame)
        except OSError as e:
            log.warning(f"send failed: {e}")

    # ── Incoming ──
    def _apply_incoming(self, payload: dict) -> None:
        t = payload["type"]

        if t == "text":
            text = payload["text"]
            log.info(f"← text ({len(text)} chars)")
            self._last_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
            self.cb.write_clipboard_text(text)
            return

        if t == "image":
            png_bytes = payload["png_bytes"]
            size = len(png_bytes)
            if size > IMAGE_FILE_MAX:
                log.warning(f"← image too large ({size / 1e6:.1f}MB) — skipping")
                return
            if size > IMAGE_INLINE_MAX:
                dest = resolve_filename(self.cb.CLIPSYNC_DIR, "image.png")
                dest.write_bytes(png_bytes)
                log.info(f"← large image saved to {dest}")
                self._last_hash = hashlib.md5(str(dest).encode("utf-8")).hexdigest()
                self.cb.write_clipboard_fileref(dest)
                self.cb.notify("ClipSync", f"Image saved: {dest.name}")
            else:
                self._last_hash = hashlib.md5(png_bytes).hexdigest()
                self.cb.write_clipboard_image(png_bytes)
                log.info(f"← image ({size / 1e6:.2f}MB) → clipboard")
            return

        if t == "file":
            file_bytes = payload["bytes"]
            name = payload["name"]
            dest = resolve_filename(self.cb.CLIPSYNC_DIR, name)
            dest.write_bytes(file_bytes)
            log.info(f"← file {name} → {dest}")
            self._last_hash = hashlib.md5(str(dest).encode("utf-8")).hexdigest()
            self.cb.write_clipboard_fileref(dest)
            self.cb.notify("ClipSync", f"File received: {dest.name}")
            return


# ── Standalone entry ───────────────────────────────────────────────────────────

def _autoselect_clipboard():
    if sys.platform == "darwin":
        import clipboard_mac as cb
        return cb
    if sys.platform == "win32":
        import clipboard_win as cb
        return cb
    if sys.platform.startswith("linux"):
        # Termux reports linux. Android module uses termux-clipboard-get/set.
        import clipboard_android as cb
        return cb
    raise SystemExit(f"no clipboard module for platform: {sys.platform}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ClipSync V3 client")
    parser.add_argument("--host", required=True, help="server address (IP, hostname, or Tailscale MagicDNS name)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    cb = _autoselect_clipboard()
    client = ClipSyncClient(args.host, args.port, cb)
    client.start()
    try:
        client.wait()
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
