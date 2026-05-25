# src/snapdiff/btrfs/diff.py
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from snapdiff.utils import subprocess as sp


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
    """
    Collapse duplicate records per path:
    - Each (path, change_type) pair is kept at most once.
    - If a path has both PERMISSIONS and MODIFIED records, drop PERMISSIONS.
    """
    # Track all change types seen per path
    by_path: dict[str, set[ChangeType]] = {}
    order: list[ChangeRecord] = []
    seen: set[tuple[str, ChangeType]] = set()

    for record in records:
        key = (record.path, record.change_type)
        if key not in seen:
            seen.add(key)
            by_path.setdefault(record.path, set()).add(record.change_type)
            order.append(record)

    result = []
    for record in order:
        path_types = by_path[record.path]
        if record.change_type == ChangeType.PERMISSIONS and ChangeType.MODIFIED in path_types:
            continue  # drop PERMISSIONS when MODIFIED is present for same path
        result.append(record)
    return result


def _parse_output(stdout: str) -> list[ChangeRecord]:
    records = []
    for line in stdout.splitlines():
        record = _parse_line(line)
        if record is not None:
            records.append(record)
    return records


def compute_diff(base_snapshot: str, new_snapshot: str) -> list[ChangeRecord]:
    """
    Run `btrfs send --no-data -p <base> <new> | btrfs receive --dump`.
    Returns deduplicated list of ChangeRecord. Raises DiffError, PermissionError,
    or RuntimeError (btrfs not found).
    """
    stdout, send_rc, send_stderr, receive_rc = sp.pipe(
        ["btrfs", "send", "--no-data", "-p", base_snapshot, new_snapshot],
        ["btrfs", "receive", "--dump"],
    )

    if send_rc != 0:
        if "Operation not permitted" in send_stderr or "EPERM" in send_stderr:
            raise PermissionError(
                "btrfs send requires root or CAP_SYS_ADMIN. "
                "Run the application with sudo or grant the appropriate capability."
            )
        raise DiffError(f"btrfs send failed (exit {send_rc}): {send_stderr.strip()}")

    if receive_rc != 0:
        raise DiffError(f"btrfs receive --dump failed (exit {receive_rc})")

    return _deduplicate(_parse_output(stdout))
