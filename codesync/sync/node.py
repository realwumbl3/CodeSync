"""One machine's shares: watch the disk, and speak TLS to peers on the LAN."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import queue
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..paths import is_frozen
from .. import identity
from ..link import build_link, parse_link
from ..protocol import read_message, write_message
from .files import (
    DIRECTORY_HASH,
    conflict_copy,
    file_id,
    hash_bytes,
    hash_file,
    relative_of,
    replace_file,
    safe_path,
)
from . import gitfiles
from .gitfiles import delete_rank, is_git_ephemeral, pack_part, pack_siblings, put_rank
from .ignore import IgnoreIndex
from .index import Index
from .plan import Entry, SendDelete, SendPut, SendRename, plan_sends
from .update import (
    CHUNK,
    MAX_UPDATE,
    UpgradeBuffer,
    apply_executable,
    build_dist,
    dist_executable,
    machine_key,
)
from .watch import Watcher

logger = logging.getLogger("codesync")

# The watcher publishes a settled save in about 50 ms. This pass only
# repairs notifications ReadDirectoryChangesW dropped. Walking every share
# four times a second, including .git, held a core open on a handheld.
_RESCAN_SECONDS = 30.0


def _action_rank(action) -> tuple:
    if isinstance(action, SendDelete):
        return (0, *delete_rank(action.path))
    if isinstance(action, SendPut):
        return (1, *put_rank(action.path))
    if isinstance(action, SendRename):
        return (2, *put_rank(getattr(action, "new", "")))
    return (3, 0, "")


def _peer_name(link: _Link | None) -> str:
    if link is None or not link.peer_name:
        return "peer"
    return link.peer_name

_EMPTY = object()
DEFAULT_PORT = 47331
# Keep the last names on screen across a reconnect blip.
_PEER_HOLD_S = 5.0


def arm_keepalive(sock: socket.socket) -> None:
    """Ask the OS to probe a peer that vanished without closing (sleep, Wi-Fi drop)."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "SIO_KEEPALIVE_VALS"):
            # enable, idle ms, interval ms
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 15_000, 5_000))
    except OSError:
        logger.debug("tcp keepalive unavailable", exc_info=True)


def lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        address = sock.getsockname()[0]
    except OSError:
        address = "127.0.0.1"
    finally:
        sock.close()
    if not address or address.startswith("127."):
        return "127.0.0.1"
    return address


def format_fingerprint(value: str) -> str:
    return " ".join(value[index : index + 4] for index in range(0, len(value), 4))


class _Link:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.outgoing: queue.Queue = queue.Queue()
        self.incoming: queue.Queue = queue.Queue()
        self.alive = True
        self.peer_id = ""
        self.peer_name = ""
        threading.Thread(target=self._read_loop, name="codesync-read", daemon=True).start()
        threading.Thread(target=self._write_loop, name="codesync-write", daemon=True).start()

    def send(self, header: dict, body: bytes = b"") -> None:
        if self.alive:
            self.outgoing.put((header, body))

    def read(self, timeout: float):
        try:
            item = self.incoming.get(timeout=timeout)
        except queue.Empty:
            return _EMPTY
        if item is None:
            self.alive = False
        return item

    def close(self) -> None:
        self.alive = False
        self.outgoing.put(None)
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _read_loop(self) -> None:
        try:
            while self.alive:
                self.incoming.put(read_message(self.sock))
        except Exception as exc:
            logger.info("read closed: %s", exc)
            self.incoming.put(None)
            self.alive = False

    def _write_loop(self) -> None:
        try:
            while self.alive:
                item = self.outgoing.get()
                if item is None:
                    break
                header, body = item
                write_message(self.sock, header, body)
        except Exception as exc:
            logger.info("write failed: %s", exc)
        finally:
            self.alive = False
            self.incoming.put(None)


@dataclass
class Share:
    id: str
    token: str
    root: Path
    role: str
    hub_host: str
    hub_port: int
    name: str
    paused: bool = False
    key: str = ""
    announce_id: str = ""
    announce_name: str = ""
    loopback: bool = False
    connected: bool = False
    syncing: bool = False
    detail: str = ""
    pending_fp: str | None = None
    conflicts: list[str] = field(default_factory=list)
    conns: list[_Link] = field(default_factory=list)
    shown_peers: list[str] = field(default_factory=list)
    peer_hold_until: float = 0.0
    accept_event: threading.Event = field(default_factory=threading.Event)
    wake: threading.Event = field(default_factory=threading.Event)
    ignore: IgnoreIndex | None = None
    watcher: Watcher | None = None

    def rules(self) -> IgnoreIndex:
        if self.ignore is None:
            self.ignore = IgnoreIndex(self.root)
        return self.ignore


class Node:
    def __init__(
        self,
        home: Path,
        *,
        listen_port: int | None = None,
        advertise: str | None = None,
        auto_trust: bool = False,
        machine_id: str | None = None,
        machine_name: str | None = None,
    ) -> None:
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.listen_port = DEFAULT_PORT if listen_port is None else listen_port
        self.port = self.listen_port
        self.advertise = advertise or lan_ip()
        self.auto_trust = auto_trust
        self.machine_id = machine_id or ""
        self.machine_name = machine_name or os.environ.get("COMPUTERNAME") or socket.gethostname() or "PC"
        self.trusted: set[str] = set()
        self.shares: dict[str, Share] = {}
        self.index = Index(self.home / "index.db")
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._server: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._started = False
        self._upgrade = UpgradeBuffer()
        self._spools: dict[tuple[str, str, str], dict] = {}
        self._holds: dict[tuple[str, str], tuple[str, int, int]] = {}
        # Folder deletes that arrived while the folder still had children.
        self._dir_removals: dict[tuple[str, str], tuple[str, int, dict, _Link]] = {}

    def start(self) -> None:
        if self._started:
            return
        self._load()
        self._server_tls = identity.server_context(self.home)
        self._client_tls = identity.client_context()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("0.0.0.0", self.listen_port))
        except OSError:
            if self.listen_port == 0:
                raise
            server.bind(("0.0.0.0", 0))
        server.listen(8)
        server.settimeout(0.5)
        self.port = int(server.getsockname()[1])
        self.listen_port = self.port
        self._server = server
        self._started = True
        self._save()
        for share in list(self.shares.values()):
            self._activate(share)
        self._spawn(self._accept_loop, "codesync-accept")
        self._spawn(self._poll_loop, "codesync-poll")

    def stop(self) -> None:
        self._stop.set()
        for share in list(self.shares.values()):
            share.wake.set()
            share.accept_event.set()
            self._stop_watcher(share)
            for conn in list(share.conns):
                conn.close()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        for thread in self._threads:
            thread.join(timeout=2)
        self.index.close()
        self._started = False

    def create_share(self, root: Path) -> dict:
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError("Choose a folder that exists")
        with self._lock:
            for share in self.shares.values():
                if share.root == root:
                    return self._view_locked(share)
            share = Share(
                id=uuid.uuid4().hex,
                token=_token(),
                root=root,
                role="hub",
                hub_host=self.advertise,
                hub_port=self.port,
                name=root.name or "Folder",
                detail="Waiting for someone to open the link",
            )
            share.key = share.id
            self.shares[share.key] = share
            self._save_locked()
        self._activate(share)
        return self.view(share.key)

    def join_share(self, link: str, root: Path) -> dict:
        parsed = parse_link(link)
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            for share in self.shares.values():
                if share.root == root:
                    return self._view_locked(share)
            same = [share for share in self.shares.values() if share.id == parsed.share_id]
            if same and not any(_same(share.token, parsed.token) for share in same):
                raise ValueError("This link does not match the share on this PC")
            hub = next(
                (share for share in same if share.role == "hub" and _same(share.token, parsed.token)),
                None,
            )
            # A second folder on this PC needs its own index. The first remote
            # membership keeps the protocol id so an existing index still matches.
            extra = bool(same)
            key = uuid.uuid4().hex if extra else parsed.share_id
            loopback = hub is not None
            share = Share(
                id=parsed.share_id,
                token=parsed.token,
                root=root,
                role="peer",
                hub_host="127.0.0.1" if loopback else parsed.host,
                hub_port=self.port if loopback else parsed.port,
                name=root.name or "Folder",
                key=key,
                announce_id=f"{self.machine_id}:{key[:8]}" if extra else "",
                announce_name=f"{self.machine_name} ({root.name})" if extra else "",
                loopback=loopback,
                detail="Connecting…",
            )
            self.shares[share.key] = share
            self._save_locked()
        self._activate(share)
        return self.view(share.key)

    def set_paused(self, share_id: str, paused: bool) -> None:
        with self._lock:
            share = self.shares[share_id]
            share.paused = paused
            self._save_locked()
            if paused:
                for conn in list(share.conns):
                    conn.close()
                share.conns.clear()
                share.connected = False
                share.detail = "Paused"
        if paused:
            self._stop_watcher(share)
            return
        share.rules().invalidate()
        self._consume(share, self._observe_all(share))
        self._start_watcher(share)
        share.wake.set()

    def stop_share(self, share_id: str) -> None:
        with self._lock:
            share = self.shares.pop(share_id, None)
            self._save_locked()
        if share is None:
            return
        share.paused = True
        share.wake.set()
        share.accept_event.set()
        self._stop_watcher(share)
        for conn in list(share.conns):
            conn.close()
        self.index.drop_share(self._key(share))

    def accept_fingerprint(self, share_id: str) -> None:
        with self._lock:
            share = self.shares[share_id]
            fingerprint = share.pending_fp or ""
            if fingerprint:
                self.trusted.add(fingerprint)
                share.pending_fp = None
                self._save_locked()
        share.accept_event.set()

    def dismiss_conflicts(self, share_id: str) -> None:
        with self._lock:
            self.shares[share_id].conflicts.clear()

    def view(self, share_id: str) -> dict:
        with self._lock:
            return self._view_locked(self.shares[share_id])

    def list_shares(self) -> list[dict]:
        with self._lock:
            return [self._view_locked(share) for share in self.shares.values()]

    def ignore_cached(self, share_id: str) -> dict | None:
        with self._lock:
            share = self.shares.get(share_id)
            index = None if share is None else share.ignore
        if index is None:
            return None
        return index.cached_listing()

    def ignore_listing(self, share_id: str) -> dict | None:
        with self._lock:
            share = self.shares.get(share_id)
            if share is None:
                return None
            index = share.rules()
        detected, entries = index.ignored_listing()
        return {"detected": detected, "count": len(entries), "entries": entries}

    def _view_locked(self, share: Share) -> dict:
        peers = self._stable_peers(share)
        if share.pending_fp:
            status, pill = "Confirm", "starting"
        elif share.conflicts:
            status, pill = "Conflict", "error"
        elif share.paused:
            status, pill = "Paused", "stopped"
        elif share.role == "peer" and not share.connected:
            status, pill = "Offline", "stopped"
        elif share.syncing:
            status, pill = "Syncing", "starting"
        else:
            status, pill = "In sync", "running"
        return {
            "id": self._key(share),
            "name": share.name,
            "root": str(share.root),
            "role": share.role,
            "link": self._link_for(share),
            "status": status,
            "pill": pill,
            "detail": share.detail,
            "paused": share.paused,
            "conflicts": list(share.conflicts),
            "pending_fp": format_fingerprint(share.pending_fp) if share.pending_fp else "",
            "peers": peers,
        }

    def _stable_peers(self, share: Share) -> list[str]:
        """Names to show. A short empty gap keeps the previous names."""
        if share.paused:
            share.shown_peers = []
            share.peer_hold_until = 0.0
            share.connected = False
            return []
        live = [conn.peer_name or "Connected PC" for conn in self._listed_conns(share)]
        now = time.monotonic()
        if live:
            share.shown_peers = live
            share.peer_hold_until = 0.0
            share.connected = True
            return list(live)
        if share.shown_peers:
            if share.peer_hold_until == 0.0:
                share.peer_hold_until = now + _PEER_HOLD_S
            if now < share.peer_hold_until:
                share.connected = True
                return list(share.shown_peers)
        share.shown_peers = []
        share.peer_hold_until = 0.0
        share.connected = False
        return []

    def _link_for(self, share: Share) -> str:
        if share.role == "hub":
            return build_link(self.advertise, self.port, share.id, share.token)
        return build_link(share.hub_host, share.hub_port, share.id, share.token)

    def _key(self, share: Share) -> str:
        return share.key or share.id

    def _owns(self, share: Share) -> bool:
        return self.shares.get(self._key(share)) is share

    def _announce_id(self, share: Share) -> str:
        return share.announce_id or self.machine_id

    def _announce_name(self, share: Share) -> str:
        return share.announce_name or self.machine_name

    def _activate(self, share: Share) -> None:
        share.rules().invalidate()
        self._consume(share, self._observe_all(share))
        indexed = self.index.snapshot(self._key(share))
        live = sum(1 for row in indexed.values() if not row.deleted)
        detected, ignored = share.rules().ignored_listing()
        logger.info(
            "share ready name=%s role=%s paused=%s root=%s hub=%s:%s indexed=%d gitignore=%s ignored=%d",
            share.name,
            share.role,
            share.paused,
            share.root,
            share.hub_host,
            share.hub_port,
            live,
            detected,
            len(ignored),
        )
        if not share.paused:
            self._start_watcher(share)
        if self._claim_if_self_hosted(share):
            share.role = "hub"
            share.hub_host = self.advertise
            share.hub_port = self.port
            with self._lock:
                self._save_locked()
        if not share.conns:
            self._set_presence(share)
        if share.role == "peer":
            self._spawn(lambda: self._peer_loop(share), f"codesync-peer-{share.id[:6]}")

    def _claim_if_self_hosted(self, share: Share) -> bool:
        """A peer whose hub address is this listener would dial itself.

        The saved link points at this PC, so this process is the host. An extra
        folder joined on purpose uses loopback and still dials the local hub.
        """
        if share.role != "peer" or share.loopback or share.hub_port != self.port:
            return False
        host = (share.hub_host or "").strip().lower().rstrip(".")
        if host in {"", "127.0.0.1", "localhost", "::1"}:
            return True
        return host in {self.advertise.lower(), lan_ip().lower()}

    def _listed_conns(self, share: Share) -> list[_Link]:
        live = [conn for conn in share.conns if conn.alive]
        me = machine_key(self.machine_id)
        if share.role != "hub" or not me:
            return live
        return [conn for conn in live if machine_key(conn.peer_id) != me]

    def _spawn(self, target, name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def _start_watcher(self, share: Share) -> None:
        if share.watcher is not None:
            return
        watcher = Watcher(share.root, lambda paths, moves: self._on_flush(share, paths, moves))
        share.watcher = watcher
        watcher.start()

    def _stop_watcher(self, share: Share) -> None:
        watcher = share.watcher
        share.watcher = None
        if watcher is not None:
            watcher.stop()

    def _peer_loop(self, share: Share) -> None:
        while not self._stop.is_set() and self._owns(share):
            if share.paused:
                share.wake.wait(0.3)
                share.wake.clear()
                continue
            try:
                self._connect_peer(share)
            except ConnectionRefusedError:
                if share.detail != "Host offline":
                    logger.info("hub %s:%s refused %s", share.hub_host, share.hub_port, share.name)
                share.detail = "Host offline"
                share.connected = False
            except OSError as exc:
                message = str(exc)[:160]
                if message != share.detail:
                    logger.info("hub %s:%s failed for %s: %s", share.hub_host, share.hub_port, share.name, message)
                share.detail = message
                share.connected = False
            if self._stop.is_set() or not self._owns(share):
                break
            share.wake.wait(1.5)
            share.wake.clear()

    def _connect_peer(self, share: Share) -> None:
        raw = socket.create_connection((share.hub_host, share.hub_port), timeout=8)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        arm_keepalive(raw)
        tls = self._client_tls.wrap_socket(raw, server_hostname=share.hub_host)
        try:
            peer = tls.getpeercert(binary_form=True) or b""
            fingerprint = hashlib.sha256(peer).hexdigest()
            if not self._ensure_trust(share, fingerprint):
                logger.info("hub certificate for %s was not accepted", share.name)
                return
            share.detail = "Connecting…"
            self._session(tls, initiator=True, share=share)
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def _ensure_trust(self, share: Share, fingerprint: str) -> bool:
        if self.auto_trust or share.loopback:
            with self._lock:
                self.trusted.add(fingerprint)
                self._save_locked()
            return True
        with self._lock:
            if fingerprint in self.trusted:
                return True
            share.pending_fp = fingerprint
            share.detail = "Confirm this PC"
            share.accept_event.clear()
            logger.info("waiting to confirm hub for %s fingerprint %s", share.name, fingerprint)
        while not self._stop.is_set() and self._owns(share):
            if share.accept_event.wait(0.2):
                with self._lock:
                    return fingerprint in self.trusted
        return False

    def _accept_loop(self) -> None:
        server = self._server
        if server is None:
            return
        while not self._stop.is_set():
            try:
                client, _addr = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            arm_keepalive(client)
            try:
                tls = self._server_tls.wrap_socket(client, server_side=True)
            except OSError as exc:
                logger.info("tls handshake failed: %s", exc)
                client.close()
                continue
            self._spawn(lambda sock=tls: self._session(sock, initiator=False, share=None), "codesync-session")

    def _session(self, sock: socket.socket, *, initiator: bool, share: Share | None) -> None:
        link = _Link(sock)
        current = share
        try:
            if initiator:
                if share is None:
                    return
                link.send(
                    {
                        "type": "hello",
                        "share_id": share.id,
                        "token": share.token,
                        "machine_id": self._announce_id(share),
                        "name": self._announce_name(share),
                    }
                )
                reply = self._expect(link, "hello_ok", 20)
                if reply is None:
                    logger.info("hello failed share=%s hub=%s:%s", share.name, share.hub_host, share.hub_port)
                    return
                header, _body = reply
                link.peer_id = str(header.get("machine_id") or "")
                link.peer_name = str(header.get("name") or "Peer")
                current = share
            else:
                reply = self._expect(link, "hello", 20)
                if reply is None:
                    logger.info("hello timed out")
                    return
                header, _body = reply
                with self._lock:
                    protocol_id = str(header.get("share_id") or "")
                    token = str(header.get("token") or "")
                    current = next(
                        (
                            item
                            for item in self.shares.values()
                            if item.role == "hub" and item.id == protocol_id and _same(item.token, token)
                        ),
                        None,
                    )
                    if current is None:
                        found = self.shares.get(protocol_id)
                        if (
                            found is not None
                            and found.role != "hub"
                            and found.id == protocol_id
                            and _same(found.token, token)
                        ):
                            current = found
                    paused = bool(current and current.paused)
                if current is None or paused:
                    logger.info(
                        "hello rejected share_id=%s matched=%s paused=%s",
                        protocol_id,
                        current is not None,
                        paused,
                    )
                    link.send({"type": "error", "message": "rejected"})
                    return
                link.peer_id = str(header.get("machine_id") or "")
                link.peer_name = str(header.get("name") or "Peer")
                link.send(
                    {"type": "hello_ok", "machine_id": self.machine_id, "name": self.machine_name}
                )
            with self._lock:
                current.conns.append(link)
                self._set_presence(current)
                manifest = self._manifest_locked(current)
            logger.info(
                "session open share=%s role=%s root=%s peer=%s initiator=%s manifest=%d",
                current.name,
                current.role,
                current.root,
                link.peer_name,
                initiator,
                len(manifest["entries"]),
            )
            link.send(manifest)
            while link.alive and not self._stop.is_set() and self._owns(current):
                item = link.read(0.25)
                if item is None:
                    break
                if item is _EMPTY:
                    continue
                header, body = item
                self._handle(current, link, header, body)
        except Exception:
            logger.exception("sync session failed")
        finally:
            if current is not None:
                logger.info("session closed share=%s peer=%s", current.name, link.peer_name)
                with self._lock:
                    if link in current.conns:
                        current.conns.remove(link)
                    if self._owns(current):
                        self._set_presence(current)
            link.close()

    def _expect(self, link: _Link, kind: str, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and link.alive and not self._stop.is_set():
            item = link.read(min(1.0, max(0.05, deadline - time.monotonic())))
            if item is None:
                return None
            if item is _EMPTY:
                continue
            header, body = item
            if header.get("type") == "error":
                return None
            if header.get("type") == kind:
                return header, body
        return None

    def _handle(self, share: Share, origin: _Link, header: dict, body: bytes) -> None:
        kind = header.get("type")
        if kind == "manifest":
            self._on_manifest(share, origin, header.get("entries") or [])
        elif kind == "put":
            self._apply_put(share, origin, header, body)
        elif kind == "delete":
            self._apply_delete(share, origin, header)
        elif kind == "rename":
            self._apply_rename(share, origin, header)
        elif kind == "upgrade":
            self._on_upgrade(origin, header, body)

    def broadcast_upgrade(self, on_status=None) -> str:
        if is_frozen():
            return "Updates can only be sent from the dev build"

        def report(text: str) -> None:
            if on_status is not None:
                on_status(text)

        report("Building…")
        error = build_dist()
        if error:
            return error
        path = dist_executable()
        if path is None:
            return "Build finished without dist\\CodeSync.exe"
        data = path.read_bytes()
        if not data.startswith(b"MZ") or len(data) > MAX_UPDATE:
            return "dist\\CodeSync.exe is not a usable build"
        digest = hashlib.sha256(data).hexdigest()
        parts = max(1, (len(data) + CHUNK - 1) // CHUNK)
        targets = self._upgrade_targets(None)
        if not targets:
            return "No connected clients"
        report("Sending…")
        for index in range(parts):
            body = data[index * CHUNK : (index + 1) * CHUNK]
            header = {
                "type": "upgrade",
                "sha256": digest,
                "size": len(data),
                "part": index,
                "parts": parts,
            }
            for conn in targets:
                if conn.alive:
                    conn.send(header, body)
        count = len(targets)
        return "Sent to 1 client" if count == 1 else f"Sent to {count} clients"

    def _upgrade_targets(self, origin: _Link | None) -> list[_Link]:
        skip = machine_key(origin.peer_id) if origin is not None and origin.peer_id else ""
        chosen: dict[str, _Link] = {}
        with self._lock:
            conns = [conn for share in self.shares.values() for conn in share.conns if conn.alive]
        for conn in conns:
            key = machine_key(conn.peer_id) or str(id(conn))
            if skip and key == skip:
                continue
            chosen.setdefault(key, conn)
        return list(chosen.values())

    def _on_upgrade(self, origin: _Link, header: dict, body: bytes) -> None:
        with self._lock:
            before = self._upgrade.next_part
            payload = self._upgrade.feed(header, body)
            accepted = payload is not None or self._upgrade.next_part != before
        if accepted and not is_frozen():
            forwarded = dict(header)
            for conn in self._upgrade_targets(origin):
                if conn.alive:
                    conn.send(forwarded, body)
        if payload is not None:
            try:
                apply_executable(payload)
            except Exception:
                logger.exception("upgrade failed")

    def _on_manifest(self, share: Share, origin: _Link, entries) -> None:
        remote: dict[str, Entry] = {}
        if not isinstance(entries, list):
            logger.info("manifest from %s ignored: entries were not a list", origin.peer_name)
            return
        skipped = 0
        for item in entries:
            if not isinstance(item, dict):
                skipped += 1
                continue
            path = str(item.get("path") or "")
            try:
                safe_path(share.root, path)
            except ValueError:
                skipped += 1
                continue
            remote[path] = Entry(
                str(item.get("hash") or ""),
                int(item.get("counter") or 0),
                bool(item.get("deleted")),
            )
        with self._lock:
            local = {
                path: Entry(row.hash, row.counter, row.deleted)
                for path, row in self.index.snapshot(self._key(share)).items()
            }
            aligns, actions = plan_sends(local, remote, self._announce_id(share), origin.peer_id or "")
            for path, counter in aligns:
                self.index.set_counter(self._key(share), path, counter)
            share.syncing = bool(actions)
        puts = sum(isinstance(action, SendPut) for action in actions)
        deletes = sum(isinstance(action, SendDelete) for action in actions)
        renames = sum(isinstance(action, SendRename) for action in actions)
        logger.info(
            "manifest from %s share=%s remote=%d local=%d skipped=%d put=%d delete=%d rename=%d align=%d",
            origin.peer_name,
            share.name,
            len(remote),
            len(local),
            skipped,
            puts,
            deletes,
            renames,
            len(aligns),
        )
        if not actions:
            logger.info("manifest from %s share=%s: nothing to send", origin.peer_name, share.name)
        try:
            for action in sorted(actions, key=_action_rank):
                if isinstance(action, SendPut):
                    self._send_put(origin, share, action.path, action.hash, action.counter)
                elif isinstance(action, SendDelete):
                    self._send_one(
                        origin,
                        {
                            "type": "delete",
                            "path": action.path,
                            "hash": action.hash,
                            "counter": action.counter,
                        },
                    )
                elif isinstance(action, SendRename):
                    self._send_one(
                        origin,
                        {
                            "type": "rename",
                            "old": action.old,
                            "new": action.new,
                            "hash": action.hash,
                            "counter": action.counter,
                        },
                    )
        finally:
            share.syncing = False

    def _apply_put(self, share: Share, origin: _Link, header: dict, body: bytes) -> None:
        rel = str(header.get("path") or "")
        digest = str(header.get("hash") or "")
        counter = int(header.get("counter") or 0)
        peer = _peer_name(origin)
        try:
            path = safe_path(share.root, rel)
            parts = int(header.get("parts") or 1)
            part = int(header.get("part") or 0)
            size = int(header.get("size") if header.get("size") is not None else len(body))
        except (TypeError, ValueError):
            logger.info("recv put %s from %s skipped: unsafe path", rel, peer)
            return
        directory = digest == DIRECTORY_HASH
        if share.rules().is_ignored(rel, directory) or is_git_ephemeral(rel):
            logger.info("recv put %s from %s skipped: ignored", rel, peer)
            return
        if directory:
            self._apply_directory(share, origin, header, body, rel, path, digest, counter, peer)
            return
        if parts > 1:
            self._relay(share, origin, header, body)
            staged = self._take_chunk(share, path, rel, digest, size, part, parts, body)
            if staged is None:
                return
            self._install_staged(share, rel, path, staged, digest, counter, peer)
            return
        if hash_bytes(body) != digest:
            logger.info("recv put %s from %s skipped: hash mismatch bytes=%d", rel, peer, len(body))
            return
        with self._lock:
            if self._put_is_current(share, origin, header, body, rel, digest, counter, peer):
                return
            if self._hold_pack_side(share, rel, path, body, digest, counter, len(body)):
                self._relay(share, origin, header, body)
                return
            if not self._displace_empty_dir(path):
                logger.info("recv put %s from %s skipped: a non-empty folder is in the way", rel, peer)
                return
            replace_file(path, body)
            self._record_put(share, rel, path, digest, counter)
            self._relay(share, origin, header, body)
            rules_changed = _is_ignore_file(rel)
            logger.info("recv put %s from %s wrote bytes=%d counter=%d", rel, peer, len(body), counter)
        if rules_changed:
            share.rules().invalidate()
            self._consume(share, self._observe_all(share))

    def _put_is_current(
        self,
        share: Share,
        origin: _Link,
        header: dict,
        body: bytes,
        rel: str,
        digest: str,
        counter: int,
        peer: str,
    ) -> bool:
        row = self.index.get(self._key(share), rel)
        if row and not row.deleted and row.counter > counter:
            logger.info(
                "recv put %s from %s skipped: local counter %d > %d",
                rel,
                peer,
                row.counter,
                counter,
            )
            return True
        if row and not row.deleted and row.hash == digest:
            if counter > row.counter:
                self.index.set_counter(self._key(share), rel, counter)
            self._relay(share, origin, header, body)
            logger.info("recv put %s from %s unchanged counter=%d", rel, peer, counter)
            return True
        if row and not row.deleted and row.hash != digest and row.counter == counter:
            try:
                path = safe_path(share.root, rel)
            except ValueError:
                return True
            self._keep_conflict(share, path, peer)
            logger.info("recv put %s from %s conflict counter=%d", rel, peer, counter)
        return False

    def _take_chunk(
        self,
        share: Share,
        path: Path,
        rel: str,
        digest: str,
        size: int,
        part: int,
        parts: int,
        body: bytes,
    ) -> Path | None:
        if part < 0 or part >= parts or size < 0 or part * gitfiles.PUT_CHUNK + len(body) > size:
            return None
        with self._lock:
            return self._write_chunk(share, path, rel, digest, size, part, parts, body)

    def _write_chunk(
        self,
        share: Share,
        path: Path,
        rel: str,
        digest: str,
        size: int,
        part: int,
        parts: int,
        body: bytes,
    ) -> Path | None:
        key = (self._key(share), rel, digest)
        slot = self._spools.get(key)
        spool = path.with_name(path.name + ".codesync-part")
        if slot is None or slot["size"] != size or slot["parts"] != parts:
            spool.parent.mkdir(parents=True, exist_ok=True)
            with spool.open("wb") as handle:
                if size > 0:
                    handle.seek(size - 1)
                    handle.write(b"\0")
            slot = {"spool": spool, "size": size, "parts": parts, "got": set()}
            self._spools[key] = slot
        if part not in slot["got"]:
            with slot["spool"].open("r+b") as handle:
                handle.seek(part * gitfiles.PUT_CHUNK)
                handle.write(body)
            slot["got"].add(part)
        if len(slot["got"]) != parts:
            return None
        self._spools.pop(key, None)
        return slot["spool"]

    def _install_staged(
        self,
        share: Share,
        rel: str,
        path: Path,
        staged: Path,
        digest: str,
        counter: int,
        peer: str,
    ) -> None:
        try:
            checked, size = hash_file(staged)
        except OSError:
            logger.info("recv put %s from %s skipped: could not read assembled file", rel, peer)
            return
        if checked != digest:
            logger.info("recv put %s from %s skipped: assembled hash mismatch", rel, peer)
            staged.unlink(missing_ok=True)
            return
        with self._lock:
            row = self.index.get(self._key(share), rel)
            if row and not row.deleted and row.counter > counter:
                staged.unlink(missing_ok=True)
                return
            if row and not row.deleted and row.hash == digest:
                if counter > row.counter:
                    self.index.set_counter(self._key(share), rel, counter)
                staged.unlink(missing_ok=True)
                return
            if row and not row.deleted and row.hash != digest and row.counter == counter:
                self._keep_conflict(share, path, peer)
            if self._hold_pack_side(share, rel, path, staged, digest, counter, size):
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            if not self._displace_empty_dir(path):
                staged.unlink(missing_ok=True)
                logger.info("recv put %s from %s skipped: a non-empty folder is in the way", rel, peer)
                return
            os.replace(staged, path)
            self._record_put(share, rel, path, digest, counter)
            logger.info("recv put %s from %s wrote bytes=%d counter=%d", rel, peer, size, counter)
            rules_changed = _is_ignore_file(rel)
        if rules_changed:
            share.rules().invalidate()
            self._consume(share, self._observe_all(share))

    def _hold_pack_side(
        self,
        share: Share,
        rel: str,
        path: Path,
        payload: bytes | Path,
        digest: str,
        counter: int,
        size: int,
    ) -> bool:
        part = pack_part(rel)
        if part is None or part[1] == ".pack":
            return False
        if safe_path(share.root, part[0]).is_file():
            return False
        hold = path.with_name(path.name + ".codesync-hold")
        hold.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, Path):
            os.replace(payload, hold)
        else:
            hold.write_bytes(payload)
        self._holds[(self._key(share), rel)] = (digest, counter, size)
        logger.info("held %s until %s is on disk", rel, part[0])
        return True

    def _record_put(self, share: Share, rel: str, path: Path, digest: str, counter: int) -> None:
        stat = path.stat()
        self.index.upsert(
            self._key(share), rel, digest, stat.st_size, stat.st_mtime_ns, counter, file_id(path)
        )
        part = pack_part(rel)
        if part is not None and part[1] == ".pack":
            self._flush_holds(share, part[0])

    def _flush_holds(self, share: Share, pack_rel: str) -> None:
        prefix = pack_rel[: -len(".pack")]
        share_key = self._key(share)
        for (held_share, rel), (digest, counter, _size) in list(self._holds.items()):
            if held_share != share_key or not rel.startswith(prefix + "."):
                continue
            try:
                path = safe_path(share.root, rel)
            except ValueError:
                continue
            hold = path.with_name(path.name + ".codesync-hold")
            self._holds.pop((held_share, rel), None)
            if not hold.is_file():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(hold, path)
            stat = path.stat()
            self.index.upsert(
                share_key, rel, digest, stat.st_size, stat.st_mtime_ns, counter, file_id(path)
            )
            logger.info("installed held %s after %s", rel, pack_rel)

    def _unlink_pack_siblings(self, share: Share, rel: str) -> None:
        for sibling in pack_siblings(rel):
            self._holds.pop((self._key(share), sibling), None)
            try:
                path = safe_path(share.root, sibling)
            except ValueError:
                continue
            hold = path.with_name(path.name + ".codesync-hold")
            for candidate in (path, hold):
                try:
                    if candidate.is_file():
                        candidate.unlink()
                except OSError:
                    logger.info("could not delete %s", candidate, exc_info=True)

    def _apply_directory(
        self,
        share: Share,
        origin: _Link,
        header: dict,
        body: bytes,
        rel: str,
        path: Path,
        digest: str,
        counter: int,
        peer: str,
    ) -> None:
        if body:
            logger.info("recv put %s from %s skipped: folder payload", rel, peer)
            return
        with self._lock:
            row = self.index.get(self._key(share), rel)
            if self._put_is_current(share, origin, header, body, rel, digest, counter, peer):
                return
            if path.is_symlink() or path.is_file():
                if not (row and not row.deleted and row.hash != digest and row.counter == counter):
                    self._keep_conflict(share, path, peer)
                try:
                    path.unlink()
                except OSError:
                    logger.info("recv put %s from %s skipped: could not replace a file", rel, peer)
                    return
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                logger.info("recv put %s from %s skipped: could not create folder", rel, peer)
                return
            if not path.is_dir():
                logger.info("recv put %s from %s skipped: folder missing after create", rel, peer)
                return
            self._record_put(share, rel, path, digest, counter)
            self._relay(share, origin, header, body)
            logger.info("recv put %s from %s created folder counter=%d", rel, peer, counter)

    def _displace_empty_dir(self, path: Path) -> bool:
        """Move an empty folder aside so a file can take its path."""
        if path.is_symlink() or not path.is_dir():
            return True
        try:
            path.rmdir()
        except OSError:
            return False
        return True

    def _apply_delete(self, share: Share, origin: _Link, header: dict) -> None:
        rel = str(header.get("path") or "")
        digest = str(header.get("hash") or "")
        counter = int(header.get("counter") or 0)
        peer = _peer_name(origin)
        if is_git_ephemeral(rel):
            logger.info("recv delete %s from %s skipped: git lock", rel, peer)
            return
        try:
            path = safe_path(share.root, rel)
        except ValueError:
            logger.info("recv delete %s from %s skipped: unsafe path", rel, peer)
            return
        with self._lock:
            row = self.index.get(self._key(share), rel)
            if row is None:
                logger.info("recv delete %s from %s skipped: not tracked", rel, peer)
                return
            if row.counter > counter:
                logger.info(
                    "recv delete %s from %s skipped: local counter %d > %d",
                    rel,
                    peer,
                    row.counter,
                    counter,
                )
                return
            if row.deleted:
                if counter > row.counter:
                    self.index.tombstone(self._key(share), rel, row.hash, counter)
                logger.info("recv delete %s from %s already deleted", rel, peer)
                return
            if row.hash != digest:
                self._keep_conflict(share, path, peer)
                logger.info("recv delete %s from %s conflict counter=%d", rel, peer, counter)
            if not self._remove_at(path):
                if path.is_dir() and not path.is_symlink():
                    self._dir_removals[(self._key(share), rel)] = (
                        digest or row.hash,
                        counter,
                        dict(header),
                        origin,
                    )
                    logger.info("recv delete %s from %s waiting until the folder is empty", rel, peer)
                else:
                    logger.info("could not delete %s", rel)
                return
            self._finish_delete(share, origin, header, rel, path, digest or row.hash, counter, peer)
            self._retry_dir_removals(share)

    def _finish_delete(
        self,
        share: Share,
        origin: _Link,
        header: dict,
        rel: str,
        path: Path,
        digest: str,
        counter: int,
        peer: str,
    ) -> None:
        self._dir_removals.pop((self._key(share), rel), None)
        self._unlink_pack_siblings(share, rel)
        self.index.tombstone(self._key(share), rel, digest, counter)
        self._prune_untracked_empty(share, path.parent)
        self._relay(share, origin, header, b"")
        logger.info("recv delete %s from %s removed counter=%d", rel, peer, counter)

    def _retry_dir_removals(self, share: Share) -> None:
        share_key = self._key(share)
        while True:
            pending = [item for item in self._dir_removals.items() if item[0][0] == share_key]
            pending.sort(key=lambda item: delete_rank(item[0][1]))
            progressed = False
            for (key, rel), (digest, counter, header, origin) in pending:
                row = self.index.get(share_key, rel)
                if row is None or row.deleted or row.counter > counter:
                    self._dir_removals.pop((key, rel), None)
                    continue
                try:
                    path = safe_path(share.root, rel)
                except ValueError:
                    self._dir_removals.pop((key, rel), None)
                    continue
                if not self._remove_at(path):
                    continue
                self._finish_delete(
                    share, origin, header, rel, path, digest or row.hash, counter, _peer_name(origin)
                )
                progressed = True
                break
            if not progressed:
                return

    def _remove_at(self, path: Path) -> bool:
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def _prune_untracked_empty(self, share: Share, directory: Path) -> None:
        """Drop parent folders that exist only because a removed file created them."""
        current = directory
        root = share.root
        while current != root and root in current.parents:
            rel = relative_of(root, current)
            if rel is None:
                return
            row = self.index.get(self._key(share), rel)
            if row is not None and not row.deleted:
                return
            try:
                if any(current.iterdir()):
                    return
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def _apply_rename(self, share: Share, origin: _Link, header: dict) -> None:
        old = str(header.get("old") or "")
        new = str(header.get("new") or "")
        digest = str(header.get("hash") or "")
        counter = int(header.get("counter") or 0)
        peer = _peer_name(origin)
        try:
            old_path = safe_path(share.root, old)
            new_path = safe_path(share.root, new)
        except ValueError:
            logger.info("recv rename %s -> %s from %s skipped: unsafe path", old, new, peer)
            return
        if share.rules().is_ignored(new, False):
            logger.info("recv rename %s -> %s from %s skipped: ignored", old, new, peer)
            return
        with self._lock:
            old_row = self.index.get(self._key(share), old)
            new_row = self.index.get(self._key(share), new)
            if new_row and not new_row.deleted and new_row.counter >= counter:
                if (
                    old_row
                    and not old_row.deleted
                    and old_row.hash == digest
                    and old_row.counter < counter
                ):
                    self._unlink(old_path)
                    self.index.tombstone(self._key(share), old, digest, counter)
                logger.info("recv rename %s -> %s from %s skipped: destination is newer", old, new, peer)
                return
            if old_row is None or old_row.deleted or old_row.hash != digest or old_row.counter > counter:
                logger.info("recv rename %s -> %s from %s skipped: source does not match", old, new, peer)
                return
            new_path.parent.mkdir(parents=True, exist_ok=True)
            if new_path.is_file():
                try:
                    current, _size = hash_file(new_path)
                except OSError:
                    current = ""
                if current and current != digest:
                    self._keep_conflict(share, new_path, peer)
            if old_path.is_file():
                os.replace(old_path, new_path)
            if not new_path.is_file():
                logger.info("recv rename %s -> %s from %s skipped: file missing after rename", old, new, peer)
                return
            stat = new_path.stat()
            self.index.tombstone(self._key(share), old, digest, counter)
            self.index.upsert(
                self._key(share), new, digest, stat.st_size, stat.st_mtime_ns, counter, file_id(new_path)
            )
            self._prune_untracked_empty(share, old_path.parent)
            self._retry_dir_removals(share)
            self._relay(share, origin, header, b"")
            logger.info("recv rename %s -> %s from %s counter=%d", old, new, peer, counter)

    def _keep_conflict(self, share: Share, path: Path, peer_name: str) -> None:
        copied = conflict_copy(path, peer_name or "peer")
        if copied is None:
            logger.info("conflict %s from %s: could not keep a copy", path, peer_name or "peer")
            return
        rel = relative_of(share.root, copied) or copied.name
        share.conflicts.append(rel)
        del share.conflicts[:-20]
        logger.info("conflict %s from %s kept as %s", path.name, peer_name or "peer", rel)

    def _relay(self, share: Share, origin: _Link, header: dict, body: bytes) -> None:
        for conn in list(share.conns):
            if conn is not origin and conn.alive:
                conn.send(header, body)

    def _poll_loop(self) -> None:
        while not self._stop.wait(_RESCAN_SECONDS):
            with self._lock:
                shares = [share for share in self.shares.values() if not share.paused]
            for share in shares:
                try:
                    self._consume(share, self._observe_all(share))
                except Exception:
                    logger.exception("poll failed for %s", share.root)

    def _on_flush(self, share: Share, paths: list[str], moves: list[tuple[str, str, bool]]) -> None:
        if share.paused or not self._owns(share):
            return
        rels: list[str] = []
        for path in paths:
            rel = relative_of(share.root, Path(path))
            if rel:
                rels.append(rel)
        move_rels: list[tuple[str, str, bool]] = []
        for src, dest, is_dir in moves:
            old = relative_of(share.root, Path(src))
            new = relative_of(share.root, Path(dest))
            if old and new:
                move_rels.append((old, new, is_dir))
        if any(_is_ignore_file(rel) for rel in rels):
            share.rules().invalidate()
            self._consume(share, self._observe_all(share))
            return
        self._consume(share, self._observe_rels(share, rels, move_rels))

    def _observe_all(self, share: Share) -> dict[str, tuple]:
        found = self._walk(share)
        for rel, row in self.index.snapshot(self._key(share)).items():
            if row.deleted or rel in found:
                continue
            found[rel] = ("gone",)
        return found

    def _walk(self, share: Share) -> dict[str, tuple]:
        found: dict[str, tuple] = {}
        known = self.index.snapshot(self._key(share))
        rules = share.rules()
        stack: list[tuple[str, Path]] = [("", share.root)]
        while stack:
            rel_dir, directory = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                path = Path(entry.path)
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if rules.is_ignored(rel, True):
                            continue
                        try:
                            stat = entry.stat(follow_symlinks=False)
                            found[rel] = self._dir_tuple(path, known.get(rel), stat)
                        except OSError:
                            self._retry_soon(share, path)
                        stack.append((rel, path))
                        continue
                    if rules.is_ignored(rel, False):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    self._retry_soon(share, path)
                    continue
                try:
                    found[rel] = self._file_tuple(path, known.get(rel), stat)
                except OSError:
                    self._retry_soon(share, path)
        return found

    def _observe_rels(self, share: Share, rels: list[str], moves: list[tuple[str, str, bool]]) -> dict[str, tuple]:
        wanted = set(rels)
        for old, new, is_dir in moves:
            wanted.add(old)
            wanted.add(new)
            if is_dir:
                for row in self.index.under(self._key(share), old):
                    suffix = row.path[len(old) :]
                    wanted.add(row.path)
                    wanted.add(new + suffix)
        extras: list[str] = []
        for rel in list(wanted):
            try:
                path = safe_path(share.root, rel)
            except ValueError:
                continue
            if not path.exists():
                extras.extend(row.path for row in self.index.under(self._key(share), rel))
        wanted.update(extras)
        known = self.index.snapshot(self._key(share))
        obs: dict[str, tuple] = {}
        rules = share.rules()
        for rel in wanted:
            try:
                path = safe_path(share.root, rel)
            except ValueError:
                continue
            if path.is_symlink():
                obs[rel] = ("gone",)
                continue
            if path.is_dir():
                if rules.is_ignored(rel, True):
                    obs[rel] = ("gone",)
                    continue
                try:
                    obs[rel] = self._dir_tuple(path, known.get(rel))
                except OSError:
                    self._retry_soon(share, path)
                continue
            if (not path.exists()) or rules.is_ignored(rel, False):
                obs[rel] = ("gone",)
                continue
            try:
                obs[rel] = self._file_tuple(path, known.get(rel))
            except OSError:
                self._retry_soon(share, path)
                continue
        return obs

    def _retry_soon(self, share: Share, path: Path) -> None:
        watcher = share.watcher
        if watcher is not None and not share.paused:
            watcher.mark(str(path))

    def _file_tuple(self, path: Path, row, stat=None) -> tuple:
        if stat is None:
            stat = path.stat()
        if (
            row is not None
            and not row.deleted
            and row.mtime == stat.st_mtime_ns
            and row.size == stat.st_size
            and row.hash
        ):
            return ("file", row.hash, row.size, row.mtime, row.file_id)
        digest, size = hash_file(path)
        after = path.stat()
        if after.st_mtime_ns != stat.st_mtime_ns or after.st_size != stat.st_size or after.st_size != size:
            raise OSError("file changed while hashing")
        return ("file", digest, size, after.st_mtime_ns, file_id(path))

    def _dir_tuple(self, path: Path, row, stat=None) -> tuple:
        if stat is None:
            stat = path.stat()
        mtime = stat.st_mtime_ns
        if (
            row is not None
            and not row.deleted
            and row.hash == DIRECTORY_HASH
            and row.mtime == mtime
            and row.file_id
        ):
            return ("dir", DIRECTORY_HASH, 0, mtime, row.file_id)
        return ("dir", DIRECTORY_HASH, 0, mtime, file_id(path))

    def _consume(self, share: Share, obs: dict[str, tuple]) -> None:
        with self._lock:
            if not self._owns(share):
                return
            fresh_gone: list[tuple[str, object]] = []
            fresh_file: list[tuple] = []
            rows = self.index.snapshot(self._key(share))
            for rel, item in obs.items():
                if not self._observation_holds(share, rel, item):
                    continue
                row = rows.get(rel)
                if item[0] == "gone":
                    if row is None or row.deleted:
                        continue
                    fresh_gone.append((rel, row))
                    continue
                digest, size, mtime, fid = item[1], item[2], item[3], item[4]
                if row and not row.deleted and row.hash == digest:
                    if row.mtime != mtime or row.file_id != fid:
                        self.index.touch(self._key(share), rel, mtime, fid or row.file_id)
                    continue
                fresh_file.append((rel, digest, size, mtime, fid, row))

            gone_left = {rel: row for rel, row in fresh_gone}
            paired: set[str] = set()
            pairs: list[tuple[str, str, object]] = []
            by_id: dict[str, list[str]] = {}
            for rel, _digest, _size, _mtime, fid, row in fresh_file:
                if fid and (row is None or row.deleted):
                    by_id.setdefault(fid, []).append(rel)
            for rel, row in list(gone_left.items()):
                if not row.file_id or row.hash == DIRECTORY_HASH:
                    continue
                dests = [dest for dest in by_id.get(row.file_id, []) if dest != rel]
                if len(dests) == 1:
                    pairs.append((rel, dests[0], row))
                    del gone_left[rel]
                    paired.add(dests[0])
            puts_by_hash: dict[str, list[str]] = {}
            for rel, digest, _size, _mtime, _fid, row in fresh_file:
                if rel in paired or (row and not row.deleted) or digest == DIRECTORY_HASH:
                    continue
                puts_by_hash.setdefault(digest, []).append(rel)
            for rel, row in list(gone_left.items()):
                dests = [dest for dest in puts_by_hash.get(row.hash, []) if dest not in paired]
                if len(dests) == 1:
                    pairs.append((rel, dests[0], row))
                    del gone_left[rel]
                    paired.add(dests[0])
            meta = {
                rel: (digest, size, mtime, fid)
                for rel, digest, size, mtime, fid, _row in fresh_file
            }
            for old, new, row in pairs:
                digest, size, mtime, fid = meta[new]
                self._commit_rename(share, old, new, row, digest, size, mtime, fid)
            for rel, row in sorted(gone_left.items(), key=lambda item: delete_rank(item[0])):
                self._commit_delete(share, rel, row)
            fresh_file.sort(key=lambda item: put_rank(item[0]))
            for rel, digest, size, mtime, fid, row in fresh_file:
                if rel in paired:
                    continue
                self._commit_put(share, rel, digest, size, mtime, fid, row)

    def _observation_holds(self, share: Share, rel: str, item: tuple) -> bool:
        try:
            path = _child_path(share.root, rel)
        except ValueError:
            return False
        if item[0] == "gone":
            if path.is_symlink():
                return True
            if path.is_dir():
                return share.rules().is_ignored(rel, True)
            if path.is_file():
                return share.rules().is_ignored(rel, False)
            return True
        if item[0] == "dir":
            return path.is_dir() and not path.is_symlink() and not share.rules().is_ignored(rel, True)
        if not path.is_file():
            return False
        try:
            stat = path.stat()
        except OSError:
            return False
        return stat.st_mtime_ns == item[3] and stat.st_size == item[2]

    def _commit_put(self, share, rel, digest, size, mtime, fid, row) -> None:
        counter = (row.counter if row is not None else 0) + 1
        self.index.upsert(self._key(share), rel, digest, size, mtime, counter, fid)
        logger.info("local put %s bytes=%d counter=%d", rel, size, counter)
        self._send_put_all(share, rel, digest, counter)

    def _commit_delete(self, share, rel, row) -> None:
        counter = row.counter + 1
        self._unlink(safe_path(share.root, rel))
        self.index.tombstone(self._key(share), rel, row.hash, counter)
        logger.info("local delete %s counter=%d", rel, counter)
        self._send_all(
            share,
            {"type": "delete", "path": rel, "hash": row.hash, "counter": counter},
        )

    def _commit_rename(self, share, old, new, row, digest, size, mtime, fid) -> None:
        if row.hash == digest:
            counter = row.counter + 1
            self.index.tombstone(self._key(share), old, row.hash, counter)
            self.index.upsert(self._key(share), new, digest, size, mtime, counter, fid)
            logger.info("local rename %s -> %s counter=%d", old, new, counter)
            self._send_all(
                share,
                {"type": "rename", "old": old, "new": new, "hash": digest, "counter": counter},
            )
            return
        moved = row.counter + 1
        edited = row.counter + 2
        self.index.tombstone(self._key(share), old, row.hash, moved)
        self.index.upsert(self._key(share), new, digest, size, mtime, edited, fid)
        logger.info("local rename %s -> %s counter=%d then edit counter=%d", old, new, moved, edited)
        self._send_all(
            share,
            {"type": "rename", "old": old, "new": new, "hash": row.hash, "counter": moved},
        )
        self._send_put_all(share, new, digest, edited)

    def _send_put(self, conn: _Link, share: Share, rel: str, digest: str, counter: int) -> None:
        self._emit_put([conn], share, rel, digest, counter)

    def _send_put_all(self, share: Share, rel: str, digest: str, counter: int) -> None:
        conns = [conn for conn in list(share.conns) if conn.alive]
        if not conns:
            logger.info("send put %s not sent: no peer connected", rel)
            return
        self._emit_put(conns, share, rel, digest, counter)

    def _emit_put(self, conns: list[_Link], share: Share, rel: str, digest: str, counter: int) -> None:
        try:
            path = safe_path(share.root, rel)
        except ValueError as exc:
            logger.info("send put %s failed: %s", rel, exc)
            return
        if path.is_dir() and not path.is_symlink():
            if digest != DIRECTORY_HASH:
                logger.info("send put %s skipped: folder changed before send", rel)
                return
            header = {
                "type": "put",
                "path": rel,
                "hash": DIRECTORY_HASH,
                "counter": counter,
                "size": 0,
                "part": 0,
                "parts": 1,
            }
            for conn in conns:
                self._send_one(conn, header, b"")
            return
        try:
            digest_now, size = hash_file(path)
        except OSError as exc:
            logger.info("send put %s failed: %s", rel, exc)
            return
        if digest_now != digest:
            logger.info("send put %s skipped: file changed before send", rel)
            return
        chunk = gitfiles.PUT_CHUNK
        parts = 1 if size <= chunk else (size + chunk - 1) // chunk
        with path.open("rb") as handle:
            for part in range(parts):
                body = handle.read(size if parts == 1 else chunk)
                header = {
                    "type": "put",
                    "path": rel,
                    "hash": digest,
                    "counter": counter,
                    "size": size,
                    "part": part,
                    "parts": parts,
                }
                for conn in conns:
                    self._send_one(conn, header, body)

    def _send_all(self, share: Share, header: dict, body: bytes = b"") -> None:
        targets = [conn for conn in list(share.conns) if conn.alive]
        if not targets:
            self._log_unsent(header)
            return
        for conn in targets:
            self._send_one(conn, header, body)

    def _send_one(self, conn: _Link, header: dict, body: bytes = b"") -> None:
        kind = str(header.get("type") or "")
        if kind != "upgrade":
            path = header.get("path") or header.get("new") or ""
            origin = f" from {header.get('old')}" if kind == "rename" else ""
            logger.info(
                "send %s %s%s bytes=%d counter=%s to %s",
                kind,
                path,
                origin,
                len(body),
                header.get("counter", ""),
                conn.peer_name or conn.peer_id or "peer",
            )
        if conn.alive:
            conn.send(header, body)

    def _log_unsent(self, header: dict) -> None:
        kind = str(header.get("type") or "")
        if kind == "upgrade":
            return
        logger.info(
            "send %s %s not sent: no peer connected",
            kind,
            header.get("path") or header.get("new") or "",
        )

    def _manifest_locked(self, share: Share) -> dict:
        entries = [
            {"path": row.path, "hash": row.hash, "counter": row.counter, "deleted": row.deleted}
            for row in self.index.snapshot(self._key(share)).values()
        ]
        return {"type": "manifest", "entries": entries}

    def _set_presence(self, share: Share) -> None:
        count = len(self._listed_conns(share))
        share.connected = count > 0 or any(conn.alive for conn in share.conns)
        if share.paused:
            share.detail = "Paused"
            return
        if count:
            share.detail = "1 connected" if count == 1 else f"{count} connected"
            return
        if share.role == "hub":
            share.detail = "Waiting for someone to open the link"
            return
        if not share.detail:
            share.detail = "Connecting…"
            return
        if share.detail.endswith(" connected"):
            share.detail = "Reconnecting…"

    def _load(self) -> None:
        path = self.home / "config.json"
        if not path.is_file():
            if not self.machine_id:
                self.machine_id = str(uuid.uuid4())
            return
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        self.machine_id = str(data.get("machine_id") or self.machine_id or uuid.uuid4())
        self.trusted = set(data.get("trusted") or [])
        saved_port = data.get("listen_port")
        if self.listen_port != 0 and isinstance(saved_port, int) and saved_port > 0:
            self.listen_port = saved_port
        for item in data.get("shares") or []:
            root = Path(item["root"])
            share = Share(
                id=item["id"],
                token=item["token"],
                root=root,
                role=item.get("role") or "hub",
                hub_host=item.get("hub_host") or self.advertise,
                hub_port=int(item.get("hub_port") or self.listen_port),
                name=item.get("name") or root.name,
                paused=bool(item.get("paused")),
                key=str(item.get("key") or item["id"]),
                announce_id=str(item.get("announce_id") or ""),
                announce_name=str(item.get("announce_name") or ""),
                loopback=bool(item.get("loopback")),
            )
            self.shares[self._key(share)] = share

    def _save(self) -> None:
        with self._lock:
            self._save_locked()

    def _save_locked(self) -> None:
        import json

        payload = {
            "machine_id": self.machine_id,
            "listen_port": self.port,
            "trusted": sorted(self.trusted),
            "shares": [
                {
                    "id": share.id,
                    "token": share.token,
                    "root": str(share.root),
                    "role": share.role,
                    "hub_host": share.hub_host,
                    "hub_port": share.hub_port,
                    "name": share.name,
                    "paused": share.paused,
                    "key": self._key(share),
                    "announce_id": share.announce_id,
                    "announce_name": share.announce_name,
                    "loopback": share.loopback,
                }
                for share in self.shares.values()
            ],
        }
        target = self.home / "config.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, target)

    def _unlink(self, path: Path) -> None:
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            logger.info("could not remove %s", path, exc_info=True)


def _child_path(root: Path, rel: str) -> Path:
    """A path under root. Skips the resolve safe_path does on every file."""
    text = rel.replace("\\", "/").strip("/")
    if not text or text.startswith("/") or ":" in text:
        raise ValueError(rel)
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(rel)
    return root.joinpath(*parts)


def _token() -> str:
    import secrets

    return secrets.token_urlsafe(32)


def _same(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def _is_ignore_file(rel: str) -> bool:
    return rel == ".gitignore" or rel.endswith("/.gitignore") or rel == ".git/info/exclude"
