"""Decide which local records a peer still needs.

Both sides run this against the two manifests. The side with the higher
per-path counter sends. Equal counters with different contents are broken by
comparing machine ids, so exactly one side sends.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    hash: str
    counter: int
    deleted: bool = False


@dataclass(frozen=True)
class SendPut:
    path: str
    hash: str
    counter: int


@dataclass(frozen=True)
class SendDelete:
    path: str
    hash: str
    counter: int


@dataclass(frozen=True)
class SendRename:
    old: str
    new: str
    hash: str
    counter: int


Action = SendPut | SendDelete | SendRename


def plan_sends(
    local: dict[str, Entry],
    remote: dict[str, Entry],
    local_id: str,
    remote_id: str,
) -> tuple[list[tuple[str, int]], list[Action]]:
    aligns: list[tuple[str, int]] = []
    sends: list[Action] = []
    for path in set(local) | set(remote):
        left = local.get(path)
        right = remote.get(path)
        if left and right and left.deleted and right.deleted:
            if left.counter != right.counter:
                aligns.append((path, max(left.counter, right.counter)))
            continue
        if (
            left
            and right
            and left.hash == right.hash
            and left.deleted == right.deleted
        ):
            if left.counter != right.counter:
                aligns.append((path, max(left.counter, right.counter)))
            continue
        if _we_send(left, right, local_id, remote_id) and left is not None:
            if left.deleted:
                sends.append(SendDelete(path, left.hash, left.counter))
            else:
                sends.append(SendPut(path, left.hash, left.counter))
    return aligns, _collapse_renames(sends, remote)


def _we_send(left: Entry | None, right: Entry | None, local_id: str, remote_id: str) -> bool:
    if left is None:
        return False
    if right is None:
        return not left.deleted
    if left.counter != right.counter:
        return left.counter > right.counter
    if left.hash == right.hash and left.deleted == right.deleted:
        return False
    return local_id > remote_id


def _collapse_renames(sends: list[Action], remote: dict[str, Entry]) -> list[Action]:
    deletes = [item for item in sends if isinstance(item, SendDelete)]
    puts = [item for item in sends if isinstance(item, SendPut)]
    used_deletes: set[str] = set()
    used_puts: set[str] = set()
    renames: list[SendRename] = []
    for delete in deletes:
        same_deletes = [
            item
            for item in deletes
            if item.hash == delete.hash and item.counter == delete.counter
        ]
        candidates = [
            item
            for item in puts
            if item.hash == delete.hash
            and item.counter == delete.counter
            and item.path not in used_puts
        ]
        if len(same_deletes) != 1 or len(candidates) != 1:
            continue
        remote_old = remote.get(delete.path)
        if (
            remote_old is None
            or remote_old.deleted
            or remote_old.hash != delete.hash
            or remote_old.counter >= delete.counter
        ):
            continue
        put = candidates[0]
        renames.append(SendRename(delete.path, put.path, delete.hash, delete.counter))
        used_deletes.add(delete.path)
        used_puts.add(put.path)
    kept = [
        item
        for item in sends
        if not (
            (isinstance(item, SendDelete) and item.path in used_deletes)
            or (isinstance(item, SendPut) and item.path in used_puts)
        )
    ]
    return renames + kept
