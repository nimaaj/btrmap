# src/snapdiff/btrfs/diff.py
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class ChangeType(Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    PERMISSIONS = "permissions"


@dataclass
class ChangeRecord:
    change_type: ChangeType
    path: str
    old_path: str | None = field(default=None)


class DiffError(Exception):
    pass


_TOKEN_MAP: dict[str, ChangeType] = {
    "write": ChangeType.MODIFIED,
    "truncate": ChangeType.MODIFIED,
    "mkfile": ChangeType.CREATED,
    "mkdir": ChangeType.CREATED,
    "mksock": ChangeType.CREATED,
    "mkfifo": ChangeType.CREATED,
    "symlink": ChangeType.CREATED,
    "link": ChangeType.CREATED,
    "unlink": ChangeType.DELETED,
    "rmdir": ChangeType.DELETED,
    "rename": ChangeType.RENAMED,
    "chmod": ChangeType.PERMISSIONS,
    "chown": ChangeType.PERMISSIONS,
    "utimes": ChangeType.PERMISSIONS,
    "set_xattr": ChangeType.PERMISSIONS,
}

# Subsequent field keywords that terminate a path in btrfs receive --dump output.
_TRAILING_FIELD_RE = re.compile(
    r"\s+(?:offset|len|dest|mode|dev|uid|gid|atime|mtime|ctime|size|name|data|"
    r"clone_offset|from|root)\s+"
)


def _extract_path(text: str) -> str:
    """Strip trailing 'keyword value' pairs from a path string."""
    m = _TRAILING_FIELD_RE.search(text)
    if m:
        return text[: m.start()].strip()
    return text.strip()


def _parse_line(line: str) -> ChangeRecord | None:
    """Parse one line from `btrfs receive --dump` output. Returns None for unrecognised lines."""
    line = line.strip()
    if not line:
        return None

    parts = line.split(None, 1)
    if len(parts) < 2:
        return None

    token, rest = parts[0], parts[1].lstrip()
    change_type = _TOKEN_MAP.get(token)
    if change_type is None:
        return None

    if not rest.startswith("path "):
        return None

    path_and_rest = rest[5:]  # strip leading "path "

    if change_type == ChangeType.RENAMED:
        if " -> " not in path_and_rest:
            return None
        old_path, new_path = path_and_rest.split(" -> ", 1)
        return ChangeRecord(ChangeType.RENAMED, new_path.strip(), old_path=old_path.strip())

    return ChangeRecord(change_type, _extract_path(path_and_rest))


def _deduplicate(records: list[ChangeRecord]) -> list[ChangeRecord]:
    """If a path has both PERMISSIONS and MODIFIED records, keep only MODIFIED."""
    by_path: dict[str, ChangeRecord] = {}
    for record in records:
        key = record.path
        if key not in by_path:
            by_path[key] = record
        else:
            existing = by_path[key]
            if (
                existing.change_type == ChangeType.PERMISSIONS
                and record.change_type == ChangeType.MODIFIED
            ):
                by_path[key] = record
            # If existing is MODIFIED and new is PERMISSIONS, keep MODIFIED (no-op).
    return list(by_path.values())
