"""Enumerate read-only btrfs subvolumes via ``btrfs subvolume list``."""
from __future__ import annotations

import re
from dataclasses import dataclass

from btrmap.utils import subprocess as sp

# btrfs subvolume list -rp output format (-p adds "parent <id>" field):
# ID <id> gen <gen> parent <id> top level <lvl> path <path>
_SUBVOL_RE = re.compile(
    r"ID\s+(\d+)\s+gen\s+(\d+)(?:\s+parent\s+\d+)?\s+top level\s+\d+\s+path\s+(.+)$"
)


@dataclass(frozen=True)
class Subvolume:
    """Metadata for a single btrfs subvolume returned by ``btrfs subvolume list``."""

    id: int
    path: str          # path relative to the filesystem root passed to list_subvolumes
    mount_point: str | None
    is_readonly: bool
    generation: int


class SubvolumeListError(Exception):
    """Raised when ``btrfs subvolume list`` exits with a non-zero status."""


def list_subvolumes(fs_path: str) -> list[Subvolume]:
    """
    Run `btrfs subvolume list -rpo <fs_path>` and parse stdout.
    Returns only read-only subvolumes (guaranteed by the -r flag).
    Raises SubvolumeListError on non-zero exit.
    Raises RuntimeError if btrfs is not on PATH.
    """
    result = sp.run(["btrfs", "subvolume", "list", "-rp", fs_path])
    if result.returncode != 0:
        raise SubvolumeListError(
            f"btrfs subvolume list failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return _parse_output(result.stdout)


def _parse_output(stdout: str) -> list[Subvolume]:
    subvolumes = []
    for line in stdout.splitlines():
        m = _SUBVOL_RE.match(line.strip())
        if m:
            subvolumes.append(
                Subvolume(
                    id=int(m.group(1)),
                    generation=int(m.group(2)),
                    path=m.group(3).strip(),
                    mount_point=None,
                    is_readonly=True,  # -r flag guarantees read-only
                )
            )
    return subvolumes
