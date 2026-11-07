"""How a Git directory is copied so the peer can commit and push.

Pack indexes are useless without their pack, and a pack is often larger than
one sync message. Callers send the pack body first, in chunks, and only then
the index, refs, and the Git index.
"""

from __future__ import annotations

PUT_CHUNK = 8 * 1024 * 1024

_SIDE_SUFFIXES = (".idx", ".rev", ".bitmap", ".mtimes", ".keep", ".promisor")


def _clean(rel: str) -> str:
    return rel.replace("\\", "/").strip("/")


def inside_git(rel: str) -> bool:
    rel = _clean(rel)
    return bool(rel) and ".git" in rel.split("/")


def is_git_ephemeral(rel: str) -> bool:
    """Lock files and half-written packs inside ``.git``. Git deletes these itself."""
    rel = _clean(rel)
    if not inside_git(rel):
        return False
    name = rel.rsplit("/", 1)[-1]
    if name.endswith(".lock"):
        return True
    return name.startswith("tmp_pack_")


def pack_part(rel: str) -> tuple[str, str] | None:
    """Return ``(pack_path, suffix)`` for a file in ``.git/objects/pack``."""
    rel = _clean(rel)
    marker = ".git/objects/pack/"
    if marker not in rel and not rel.startswith(marker):
        return None
    name = rel.rsplit("/", 1)[-1]
    if not name.startswith("pack-"):
        return None
    for suffix in (".pack",) + _SIDE_SUFFIXES:
        if name.endswith(suffix):
            pack_name = name[: -len(suffix)] + ".pack"
            return rel[: -len(name)] + pack_name, suffix
    return None


def pack_siblings(pack_rel: str) -> list[str]:
    """Side files that belong to a pack body. An index without its pack breaks Git."""
    part = pack_part(pack_rel)
    if part is None or part[1] != ".pack":
        return []
    base = part[0][: -len(".pack")]
    return [base + suffix for suffix in _SIDE_SUFFIXES]


def put_rank(rel: str) -> tuple[int, str]:
    """Lower sorts first. Objects land before anything that points at them."""
    part = pack_part(rel)
    if part is not None and part[1] == ".pack":
        rank = 0
    elif part is not None:
        rank = 2
    elif not inside_git(rel):
        rank = 1
    else:
        name = _clean(rel).rsplit("/", 1)[-1]
        if name == "index":
            rank = 4
        elif name in {"HEAD", "config", "packed-refs"} or "/refs/" in f"/{_clean(rel)}/":
            rank = 3
        else:
            rank = 1
    return rank, rel


def delete_rank(rel: str) -> tuple[int, int, str]:
    """Remove a pack index before its pack, and a nested path before its parents."""
    rel = _clean(rel)
    depth = rel.count("/")
    part = pack_part(rel)
    if part is not None and part[1] == ".pack":
        kind = 2
    elif part is not None:
        kind = 0
    else:
        kind = 1
    return kind, -depth, rel
