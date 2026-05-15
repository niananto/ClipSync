"""
clipboard_server.py — Run this on your Mac
Part of: ClipSync V1 (LAN, text only)

Usage:
    python3 clipboard_server.py

Requirements:
    - No external dependencies (uses only stdlib + pbcopy/pbpaste)

V2 TODO: Add image support via NSPasteboard and base64 framing
V3 TODO: Add TLS + shared secret authentication for Tailscale mode
"""

import socket
import subprocess
import threading
import hashlib
import time
import json
import struct
import logging

# ── Config ─────────────────────────────────────────────────────────────────────
HOST = "0.0.0.0"       # Listen on all interfaces
PORT = 9999            # Change if needed; open this port in macOS firewall if necessary
POLL_INTERVAL = 0.5    # Seconds between clipboard checks
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def get_clipboard() -> str:
    """Read current Mac clipboard text via pbpaste."""
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, encoding="utf-8"
        )
        return result.stdout
    except Exception as e:
        log.warning(f"pbpaste failed: {e}")
        return ""


def set_clipboard(text: str) -> None:
    """Write text to Mac clipboard via pbcopy."""
    try:
        subprocess.run(
            ["pbcopy"], input=text, text=True, encoding="utf-8", check=True
        )
    except Exception as e:
        log.warning(f"pbcopy failed: {e}")


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
        # Read exactly 4 bytes for the length header
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


def handle_client(conn: socket.socket, addr, shared_state: dict) -> None:
    """
    Runs in a thread for each connected client.
    - Listens for clipboard updates from Windows
    - shared_state["last_hash"] is updated to prevent echo-back loops
    """
    log.info(f"Client connected: {addr}")
    shared_state["conn"] = conn

    try:
        while True:
            msg = recv_message(conn)
            if msg is None:
                log.info(f"Client disconnected: {addr}")
                break

            if msg.get("type") == "clipboard" and "text" in msg:
                incoming_text = msg["text"]
                incoming_hash = make_hash(incoming_text)

                # Only write if different from what we already have
                # This prevents the echo loop: Mac sends → Windows receives →
                # Windows sends back → Mac receives → Mac sends again → ...
                if incoming_hash != shared_state.get("last_hash"):
                    log.info(f"← Received from Windows ({len(incoming_text)} chars)")
                    shared_state["last_hash"] = incoming_hash
                    set_clipboard(incoming_text)
    except Exception as e:
        log.error(f"Client handler error: {e}")
    finally:
        shared_state["conn"] = None
        conn.close()


def clipboard_watcher(shared_state: dict) -> None:
    """
    Polls Mac clipboard every POLL_INTERVAL seconds.
    Pushes changes to Windows client if connected.
    """
    log.info("Clipboard watcher started")
    while True:
        time.sleep(POLL_INTERVAL)
        current_text = get_clipboard()
        current_hash = make_hash(current_text)

        if current_hash != shared_state.get("last_hash"):
            shared_state["last_hash"] = current_hash
            conn = shared_state.get("conn")
            if conn:
                log.info(f"→ Sending to Windows ({len(current_text)} chars)")
                ok = send_message(conn, {"type": "clipboard", "text": current_text})
                if not ok:
                    log.warning("Send failed — client may have disconnected")


def main():
    shared_state = {
        "last_hash": make_hash(get_clipboard()),  # Seed with current clipboard
        "conn": None,
    }

    # Start clipboard watcher in background thread
    watcher = threading.Thread(target=clipboard_watcher, args=(shared_state,), daemon=True)
    watcher.start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(1)  # Only expect one client (the Windows PC)
    log.info(f"ClipSync server listening on port {PORT} ...")
    log.info("Waiting for Windows client to connect...")

    while True:
        conn, addr = server.accept()
        # Handle one client at a time; if a new one connects, old one was likely dropped
        t = threading.Thread(target=handle_client, args=(conn, addr, shared_state), daemon=True)
        t.start()


if __name__ == "__main__":
    main()