"""Parse ``btrfs receive --dump`` output into typed change records.

Handles both the legacy btrfs-progs format (``path foo/bar key value``) and the
modern 6.x format (``./snapshot/foo/bar key=value``).  The public entry point is
:func:`compute_diff`, which runs the btrfs send/receive pipeline and returns a
deduplicated list of :class:`ChangeRecord` objects.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from btrmap.utils import subprocess as sp


class ChangeType(Enum):
    """The kind of change a btrfs operation represents at the file level."""

    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    PERMISSIONS = "permissions"


@dataclass
class ChangeRecord:
    """A single file-system change parsed from ``btrfs receive --dump`` output.

    For RENAMED records, ``old_path`` holds the source path and ``path`` holds
    the destination.  For all other types, ``old_path`` is ``None``.
    """

    change_type: ChangeType
    path: str
    old_path: str | None = field(default=None)


class DiffError(Exception):
    """Raised when the ``btrfs send`` or ``btrfs receive`` subprocess fails."""


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

# Field keywords that terminate a path in btrfs receive --dump output.
# Supports both old format ("offset 0") and new format ("offset=0").
_TRAILING_FIELD_RE = re.compile(
    r"\s+(?:offset|len|dest|mode|dev|uid|gid|atime|mtime|ctime|size|name|data|"
    r"clone_offset|from|root|uuid|transid|parent_uuid|parent_transid|to)"
    r"(?=[=\s])"  # look-ahead: followed by = (new) or whitespace (old)
)


def _extract_path(text: str) -> str:
    """Strip trailing 'keyword value' or 'keyword=value' pairs from a path string."""
    m = _TRAILING_FIELD_RE.search(text)
    if m:
        return text[: m.start()].strip()
    return text.strip()


def _strip_subvol_prefix(path: str) -> str:
    """Strip the leading './' and subvolume-name component from a btrfs dump path.

    Real btrfs receive --dump output uses paths like ``./snapshot/usr/bin/find``
    where ``snapshot`` is the subvolume name set by snapper. We strip that prefix
    to get the subvolume-relative path ``usr/bin/find``.
    """
    if path.startswith("./"):
        path = path[2:]
    slash = path.find("/")
    if slash >= 0:
        return path[slash + 1 :]
    return ""  # path was the subvolume root itself (e.g. "./snapshot")


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

    # Support both:
    #   old format: "path foo/bar.txt offset 0 len 1024"
    #   new format: "./snapshot/foo/bar.txt offset=0 len=1024"
    if rest.startswith("path "):
        path_and_rest = rest[5:]
        strip_prefix = False
    else:
        path_and_rest = rest
        strip_prefix = True

    if change_type == ChangeType.RENAMED:
        # Old format: "path oldname.txt -> newname.txt"
        if " -> " in path_and_rest:
            old_path, new_path = path_and_rest.split(" -> ", 1)
            return ChangeRecord(ChangeType.RENAMED, new_path.strip(), old_path=old_path.strip())
        # New format: "./snapshot/old.txt to=./snapshot/new.txt"
        to_match = re.search(r"\bto=(\S+)", path_and_rest)
        if to_match:
            old_raw = _extract_path(path_and_rest)
            new_raw = to_match.group(1)
            old_p = _strip_subvol_prefix(old_raw) if old_raw.startswith("./") else old_raw
            new_p = _strip_subvol_prefix(new_raw) if new_raw.startswith("./") else new_raw
            if not old_p or not new_p:
                return None
            return ChangeRecord(ChangeType.RENAMED, new_p, old_path=old_p)
        return None

    raw_path = _extract_path(path_and_rest)
    path = _strip_subvol_prefix(raw_path) if strip_prefix and raw_path.startswith("./") else raw_path
    if not path:
        return None

    return ChangeRecord(change_type, path)


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


def compute_diff(
    base_snapshot: str,
    new_snapshot: str,
    *,
    progress_cb: Callable[[int], None] | None = None,
) -> list[ChangeRecord]:
    """
    Run `btrfs send --no-data -p <base> <new> | btrfs receive --dump`.
    Returns deduplicated list of ChangeRecord. Raises DiffError, PermissionError,
    or RuntimeError (btrfs not found).

    progress_cb(n) is called every 50 parsed records with the running count.
    """
    records: list[ChangeRecord] = []
    count = 0

    def _on_line(line: str) -> None:
        nonlocal count
        record = _parse_line(line)
        if record is not None:
            records.append(record)
            count += 1
            if progress_cb is not None and count % 50 == 0:
                progress_cb(count)

    send_rc, send_stderr, receive_rc = sp.pipe_streaming(
        ["btrfs", "send", "--no-data", "-p", base_snapshot, new_snapshot],
        ["btrfs", "receive", "--dump"],
        _on_line,
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

    return _deduplicate(records)
