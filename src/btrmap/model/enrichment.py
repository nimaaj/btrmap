"""Populate ``size_bytes`` on each leaf of a :class:`~btrmap.model.diff_tree.DiffTree`.

Each leaf is stat-ed in the appropriate snapshot mount:

- CREATED / MODIFIED / PERMISSIONS / RENAMED (new path) → *new* snapshot mount
- DELETED (and RENAMED old path, which becomes DELETED) → *base* snapshot mount

``OSError`` failures (sockets, pipes, paths outside the mount) are silently ignored
and leave ``size_bytes`` at 0 so a single inaccessible file cannot abort the whole diff.
"""
from __future__ import annotations

import os
from collections.abc import Callable

from btrmap.btrfs.diff import ChangeType
from btrmap.model.diff_tree import DiffNode, DiffTree


def enrich(
    tree: DiffTree,
    new_snapshot_mount: str,
    base_snapshot_mount: str,
    *,
    progress_cb: Callable[[int], None] | None = None,
) -> None:
    """
    Populate size_bytes on each leaf node by stat-ing the file in the appropriate snapshot.
    Mutates the tree in place. Stat failures are silently ignored (size_bytes stays 0).

    progress_cb(n) is called every 200 files with the running count.
    """
    for i, node in enumerate(tree.iter_leaves()):
        _stat_node(node, new_snapshot_mount, base_snapshot_mount)
        if progress_cb is not None and i > 0 and i % 200 == 0:
            progress_cb(i)


def _stat_node(node: DiffNode, new_mount: str, base_mount: str) -> None:
    mount = base_mount if node.change_type == ChangeType.DELETED else new_mount
    path = os.path.join(mount, node.full_path)
    try:
        node.size_bytes = os.stat(path).st_size
    except OSError:
        node.size_bytes = 0
