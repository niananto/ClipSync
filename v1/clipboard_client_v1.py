"""
clipboard_client.py — Run this on your Windows PC
Part of: ClipSync V1 (LAN, text only)

Usage:
    python clipboard_client.py --host <MAC_LOCAL_IP>

    Example:
        python clipboard_client.py --host 192.168.1.42

Requirements:
    pip install pyperclip
    # On Windows, pyperclip uses the built-in win32 clipboard. No extra deps needed.

V2 TODO: Add image support via Pillow + base64 framing
V3 TODO: Add TLS + shared secret authentication for Tailscale mode
"""

import socket
import threading
import hashlib
import time
import json
import struct
import logging
import argparse

try:
    import pyperclip
except ImportError:
    raise SystemExit("Missing dependency: run  pip install pyperclip")

# ── Config ─────────────────────────────────────────────────────────────────────
PORT = 9999            # Must match server
POLL_INTERVAL = 0.5    # Seconds between clipboard checks
RECONNECT_DELAY = 5    # Seconds to wait before retrying connection
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def get_clipboard() -> str:
    """Read current Windows clipboard text."""
    try:
        return pyperclip.paste() or ""
    except Exception as e:
        log.warning(f"Clipboard read failed: {e}")
        return ""


def set_clipboard(text: str) -> None:
    """Write text to Windows clipboard."""
    try:
        pyperclip.copy(text)
    except Exception as e:
        log.warning(f"Clipboard write failed: {e}")


def make_hash(text: str) -> str:
    """Stable hash to detect clipboard changes."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def send_message(sock: socket.socket, payload: dict) -> bool:
    """
    Send a length-prefixed JSON message.
    Frame format: [4-byte big-endian length][JSON bytes]
    V2: extend payload type field for images
    """
    try:
        data = json.dumps(payload).encode("utf-8")
        header = struct.pack(">I", len(data))
        sock.sendall(header + data)
        return True
    except Exception:
        return False


def recv_message(sock: socket.socket) -> dict | None:
    """
    Receive a length-prefixed JSON message.
    Returns None on connection loss or error.
    """
    try:
        raw_len = _recv_exact(sock, 4)
        if raw_len is None:
            return None
        msg_len = struct.unpack(">I", raw_len)[0]

        # Safety cap — V2 will raise this for images
        if msg_len > 1_000_000:
            log.warning(f"Oversized message ({msg_len} bytes) — dropping")
            return None

        raw_data = _recv_exact(sock, msg_len)
        if raw_data is None:
            return None
        return json.loads(raw_data.decode("utf-8"))
    except Exception:
        return None


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    """Read exactly n bytes from socket, or return None on disconnect."""
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


def listen_for_updates(sock: socket.socket, shared_state: dict) -> None:
    """
    Runs in a thread.
    Listens for clipboard updates pushed from the Mac server.
    """
    while True:
        msg = recv_message(sock)
        if msg is None:
            log.info("Connection lost — listener thread exiting")
            shared_state["connected"] = False
            break

        if msg.get("type") == "clipboard" and "text" in msg:
            incoming_text = msg["text"]
            incoming_hash = make_hash(incoming_text)

            # Only write if different to prevent echo loops
            if incoming_hash != shared_state.get("last_hash"):
                log.info(f"← Received from Mac ({len(incoming_text)} chars)")
                shared_state["last_hash"] = incoming_hash
                set_clipboard(incoming_text)


def clipboard_watcher(sock: socket.socket, shared_state: dict) -> None:
    """
    Polls Windows clipboard every POLL_INTERVAL seconds.
    Pushes changes to Mac server if connected.
    """
    log.info("Clipboard watcher started")
    while shared_state.get("connected"):
        time.sleep(POLL_INTERVAL)
        current_text = get_clipboard()
        current_hash = make_hash(current_text)

        if current_hash != shared_state.get("last_hash"):
            shared_state["last_hash"] = current_hash
            log.info(f"→ Sending to Mac ({len(current_text)} chars)")
            ok = send_message(sock, {"type": "clipboard", "text": current_text})
            if not ok:
                log.warning("Send failed — connection may be lost")
                shared_state["connected"] = False
                break


def connect_and_run(host: str) -> None:
    """
    Connects to the Mac server and runs watcher + listener.
    Blocks until connection is lost, then returns (caller will retry).
    """
    shared_state = {
        "last_hash": make_hash(get_clipboard()),  # Seed with current clipboard
        "connected": True,
    }

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, PORT))
        log.info(f"Connected to Mac at {host}:{PORT}")
    except Exception as e:
        log.error(f"Connection failed: {e}")
        return

    # Listener runs in background thread
    listener = threading.Thread(
        target=listen_for_updates, args=(sock, shared_state), daemon=True
    )
    listener.start()

    # Watcher runs on main thread (blocks until disconnected)
    clipboard_watcher(sock, shared_state)

    sock.close()
    listener.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description="ClipSync V1 — Windows Client")
    parser.add_argument(
        "--host",
        required=True,
        help="Local IP of your Mac (e.g. 192.168.1.42). "
             "Find it in System Settings → Network.",
    )
    args = parser.parse_args()

    log.info(f"ClipSync client starting — target: {args.host}:{PORT}")
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