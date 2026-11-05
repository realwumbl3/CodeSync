"""codesync:// share links."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlparse


@dataclass(frozen=True)
class ShareLink:
    host: str
    port: int
    share_id: str
    token: str


def build_link(host: str, port: int, share_id: str, token: str) -> str:
    return f"codesync://{host}:{int(port)}/s/{quote(share_id, safe='')}?t={quote(token, safe='')}"


def parse_link(text: str) -> ShareLink:
    raw = (text or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "codesync" or not parsed.hostname or not parsed.port:
        raise ValueError("Enter a codesync:// link")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0] != "s" or not parts[1]:
        raise ValueError("That link is missing a share id")
    token = parse_qs(parsed.query).get("t", [""])[0]
    if not token:
        raise ValueError("That link is missing a token")
    return ShareLink(parsed.hostname, int(parsed.port), parts[1], token)
