"""Length-prefixed JSON messages with an optional file body."""

from __future__ import annotations

import json
import socket
import struct

MAX_HEADER = 8 * 1024 * 1024
MAX_BODY = 64 * 1024 * 1024


def write_message(sock: socket.socket, header: dict, body: bytes = b"") -> None:
    payload = dict(header)
    payload["n"] = len(body)
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_HEADER or len(body) > MAX_BODY:
        raise ValueError("message is too large")
    sock.sendall(struct.pack(">I", len(data)))
    sock.sendall(data)
    if body:
        view = memoryview(body)
        sent = 0
        while sent < len(view):
            sent += sock.send(view[sent:])


def _recvall(sock: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return bytes(buf)


def read_message(sock: socket.socket) -> tuple[dict, bytes]:
    raw_len = _recvall(sock, 4)
    size = struct.unpack(">I", raw_len)[0]
    if size <= 0 or size > MAX_HEADER:
        raise ValueError("invalid message header")
    header = json.loads(_recvall(sock, size).decode("utf-8"))
    if not isinstance(header, dict):
        raise ValueError("message header must be an object")
    body_len = int(header.get("n") or 0)
    if body_len < 0 or body_len > MAX_BODY:
        raise ValueError("invalid message body")
    body = _recvall(sock, body_len) if body_len else b""
    return header, body
