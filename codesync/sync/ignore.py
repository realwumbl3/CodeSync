"""Gitignore matching without requiring Git to be installed.

Rules come from ``.git/info/exclude`` and every ``.gitignore`` that sits in a
directory Git would still enter. Later rules win. An ignored directory hides
its children, which is the same cutoff Git uses.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    base: str
    negation: bool
    dir_only: bool
    regex: re.Pattern[str]

    def matches(self, full_rel: str, is_dir: bool) -> bool:
        rel = full_rel
        if self.base:
            prefix = self.base + "/"
            if not full_rel.startswith(prefix):
                return False
            rel = full_rel[len(prefix):]
        if self.dir_only and not is_dir:
            return False
        return self.regex.search(rel) is not None


def _translate(pattern: str) -> str:
    out: list[str] = []
    i = 0
    size = len(pattern)
    while i < size:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        char = pattern[i]
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(char))
            else:
                body = pattern[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                elif body.startswith("^"):
                    body = "\\" + body
                out.append("[" + body + "]")
                i = end
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


def _compile(pattern: str, anchored: bool) -> re.Pattern[str]:
    body = _translate(pattern)
    if anchored:
        return re.compile(r"\A" + body + r"\Z")
    return re.compile(r"\A(?:.+/)?(?:" + body + r")\Z")


def parse_gitignore(text: str, base: str) -> list[Rule]:
    rules: list[Rule] = []
    for raw in text.splitlines():
        if not raw or raw.startswith("#"):
            continue
        line = raw
        if line.endswith(" ") and not line.endswith("\\ "):
            line = line.rstrip(" ")
        line = line.replace("\\ ", " ")
        if not line or line == "!":
            continue
        negation = False
        if line.startswith("!"):
            negation = True
            line = line[1:]
        elif line.startswith("\\#") or line.startswith("\\!"):
            line = line[1:]
        dir_only = line.endswith("/")
        if dir_only:
            line = line[:-1]
        if not line:
            continue
        anchored = line.startswith("/") or "/" in line
        if line.startswith("/"):
            line = line[1:]
        if not line:
            continue
        rules.append(Rule(base, negation, dir_only, _compile(line, anchored)))
    return rules


class IgnoreIndex:
    """Ignore rules for one share root. ``.git`` is always ignored."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[str, list[Rule]] = {}
        self._lock = threading.Lock()
        self._gen = 0
        self._listing: tuple[int, bool, tuple[tuple[str, bool], ...]] | None = None

    def invalidate(self) -> None:
        with self._lock:
            self._gen += 1
            self._cache.clear()
            self._listing = None

    def cached_listing(self) -> dict | None:
        with self._lock:
            cached = self._listing
            gen = self._gen
        if cached is None or cached[0] != gen:
            return None
        _gen, detected, entries = cached
        return {"detected": detected, "count": len(entries), "entries": entries}

    def ignored_listing(self) -> tuple[bool, tuple[tuple[str, bool], ...]]:
        """Ignored paths from gitignore rules, pruned at ignored directories.

        ``.git`` and CodeSync temp files are not listed. The result is cached
        until ``invalidate``.
        """
        with self._lock:
            gen = self._gen
            cached = self._listing
        if cached is not None and cached[0] == gen:
            return cached[1], cached[2]
        detected, entries = self._scan(gen)
        frozen = tuple(entries)
        with self._lock:
            if self._gen == gen:
                self._listing = (gen, detected, frozen)
        return detected, frozen

    def _stale(self, gen: int) -> bool:
        with self._lock:
            return self._gen != gen

    def _scan(self, gen: int) -> tuple[bool, list[tuple[str, bool]]]:
        root = self.root
        detected = False
        entries: list[tuple[str, bool]] = []
        if not root.is_dir():
            return False, entries
        exclude = root / ".git" / "info" / "exclude"
        try:
            if (root / ".gitignore").is_file() or (exclude.is_file() and exclude.stat().st_size > 0):
                detected = True
        except OSError:
            pass
        stack: list[tuple[str, Path]] = [("", root)]
        while stack:
            if self._stale(gen):
                return detected, entries
            rel_dir, directory = stack.pop()
            try:
                with os.scandir(directory) as scan:
                    children = list(scan)
            except OSError:
                continue
            next_dirs: list[tuple[str, Path]] = []
            for entry in children:
                name = entry.name
                rel = f"{rel_dir}/{name}" if rel_dir else name
                try:
                    if entry.is_symlink():
                        continue
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if name == ".git" and not rel_dir:
                    continue
                if is_dir:
                    if self.ignored_by_rule(rel, True):
                        entries.append((rel, True))
                        continue
                    next_dirs.append((rel, Path(entry.path)))
                    continue
                if name == ".gitignore":
                    detected = True
                if name.endswith(".codesync-tmp"):
                    continue
                if self.ignored_by_rule(rel, False):
                    entries.append((rel, False))
            stack.extend(reversed(next_dirs))
        entries.sort()
        return detected, entries

    def _dir_rules(self, base: str) -> list[Rule]:
        with self._lock:
            gen = self._gen
            cached = self._cache.get(base)
        if cached is not None:
            return cached
        directory = self.root if not base else self.root.joinpath(*base.split("/"))
        rules: list[Rule] = []
        if base == "":
            exclude = directory / ".git" / "info" / "exclude"
            if exclude.is_file():
                rules.extend(parse_gitignore(_read(exclude), ""))
        gitignore = directory / ".gitignore"
        if gitignore.is_file():
            rules.extend(parse_gitignore(_read(gitignore), base))
        with self._lock:
            if self._gen != gen:
                return rules
            existing = self._cache.get(base)
            if existing is not None:
                return existing
            self._cache[base] = rules
            return rules

    def _rules_for(self, rel: str) -> list[Rule]:
        rules = list(self._dir_rules(""))
        parts = rel.split("/")
        acc: list[str] = []
        for part in parts[:-1]:
            acc.append(part)
            base = "/".join(acc)
            if _effect(rules, base, True):
                break
            rules.extend(self._dir_rules(base))
        return rules

    def is_ignored(self, rel: str, is_dir: bool) -> bool:
        rel = rel.replace("\\", "/").strip("/")
        if not rel:
            return False
        name = rel.rsplit("/", 1)[-1]
        if rel == ".git" or rel.startswith(".git/") or name.endswith(".codesync-tmp"):
            return True
        return self.ignored_by_rule(rel, is_dir)

    def ignored_by_rule(self, rel: str, is_dir: bool) -> bool:
        rel = rel.replace("\\", "/").strip("/")
        if not rel or rel == ".git" or rel.startswith(".git/"):
            return False
        parts = rel.split("/")
        for index, _part in enumerate(parts):
            partial = "/".join(parts[: index + 1])
            as_dir = is_dir if index == len(parts) - 1 else True
            if _effect(self._rules_for(partial), partial, as_dir):
                return True
        return False


def _effect(rules: list[Rule], rel: str, is_dir: bool) -> bool:
    ignored = False
    for rule in rules:
        if rule.matches(rel, is_dir):
            ignored = not rule.negation
    return ignored


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
