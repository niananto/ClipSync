"""
clipsync_server.py — ClipSync V3 relay server.

Run on the Android phone (Termux + proot Debian, alongside tailscale/jellyfin)
or on any always-on host. Pure fanout: accepts N clients, forwards each
length-prefixed frame to every *other* connected client. Never touches its
own clipboard — clipboard work happens entirely on clients.

Usage:
    python3 clipsync_server.py [--host 0.0.0.0] [--port 9999]

Requirements:
    Python 3.10+. Stdlib only.

Frame format (unchanged from V2):
    [4-byte big-endian length][JSON UTF-8]

    The server does not parse the JSON body for rebroadcast — it relays
    raw bytes for speed. It only reads the length prefix to frame the read.

Echo prevention:
    The server never sends a frame back to the client that originated it.
    Clients still use their own last_hash check to avoid feedback loops
    when a frame they emitted is later observed (it won't be, because the
    server doesn't echo it back).
"""

import argparse
import logging
import os
import socket
import struct
import threading
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
DEFAULT_HOST    = "0.0.0.0"        # Tailscale's userspace networking + ACLs
                                   # gate access; bind everywhere inside the box.
DEFAULT_PORT    = 9999
MAX_FRAME_BYTES = 70 * 1024 * 1024 # ~50MB binary + base64 overhead
LOG_DIR_DEFAULT = Path.home() / ".clipsync"
# ───────────────────────────────────────────────────────────────────────────────


class Hub:
    """Connection registry + broadcast. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[int, socket.socket] = {}
        self._labels: dict[int, str] = {}
        self._next_id = 1

    def add(self, conn: socket.socket, addr) -> int:
        with self._lock:
            cid = self._next_id
            self._next_id += 1
            self._clients[cid] = conn
            self._labels[cid] = f"{addr[0]}:{addr[1]}"
            return cid

    def remove(self, cid: int) -> None:
        with self._lock:
            self._clients.pop(cid, None)
            self._labels.pop(cid, None)

    def label(self, cid: int) -> str:
        return self._labels.get(cid, f"#{cid}")

    def peers(self, sender_cid: int) -> list[tuple[int, socket.socket]]:
        with self._lock:
            return [(cid, c) for cid, c in self._clients.items() if cid != sender_cid]

    def count(self) -> int:
        with self._lock:
            return len(self._clients)


def recv_exact(sock: socket.socket, n: int) -> bytes | None:
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


def handle_client(conn: socket.socket, addr, hub: Hub, log: logging.Logger) -> None:
    cid = hub.add(conn, addr)
    log.info(f"+ client {cid} connected from {hub.label(cid)} (total={hub.count()})")
    try:
        while True:
            header = recv_exact(conn, 4)
            if header is None:
                break
            (msg_len,) = struct.unpack(">I", header)
            if msg_len == 0 or msg_len > MAX_FRAME_BYTES:
                log.warning(f"client {cid} sent oversize frame ({msg_len} bytes) — dropping connection")
                break
            body = recv_exact(conn, msg_len)
            if body is None:
                break

            frame = header + body
            peers = hub.peers(cid)
            log.info(f"relay {msg_len + 4}B from client {cid} → {len(peers)} peer(s)")
            for pcid, pconn in peers:
                try:
                    pconn.sendall(frame)
                except OSError as e:
                    log.warning(f"send to client {pcid} failed: {e}")
                    # Don't pop here — handler thread for that peer will clean up.
    finally:
        hub.remove(cid)
        try:
            conn.close()
        except OSError:
            pass
        log.info(f"- client {cid} disconnected (total={hub.count()})")


def main() -> None:
    parser = argparse.ArgumentParser(description="ClipSync V3 relay server")
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="listen port (default 9999)")
    parser.add_argument("--log-file", default=None, help="log file path (default ~/.clipsync/server.log + stderr)")
    args = parser.parse_args()

    log_dir = Path(args.log_file).parent if args.log_file else LOG_DIR_DEFAULT
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log_file) if args.log_file else log_dir / "server.log"

    handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8"),
                                       logging.StreamHandler()]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    log = logging.getLogger("clipsync.server")

    hub = Hub()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(8)
    log.info(f"ClipSync V3 relay listening on {args.host}:{args.port} (pid={os.getpid()})")
    log.info(f"log file: {log_path}")

    try:
        while True:
            conn, addr = srv.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr, hub, log), daemon=True)
            t.start()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        try:
            srv.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
