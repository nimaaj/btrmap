# src/snapdiff/model/enrichment.py
from __future__ import annotations

import os

from snapdiff.btrfs.diff import ChangeType
from snapdiff.model.diff_tree import DiffNode, DiffTree


def enrich(tree: DiffTree, new_snapshot_mount: str, base_snapshot_mount: str) -> None:
    """
    Populate size_bytes on each leaf node by stat-ing the file in the appropriate snapshot.
    Mutates the tree in place. Stat failures are silently ignored (size_bytes stays 0).
    """
    for node in tree.iter_leaves():
        _stat_node(node, new_snapshot_mount, base_snapshot_mount)


def _stat_node(node: DiffNode, new_mount: str, base_mount: str) -> None:
    mount = base_mount if node.change_type == ChangeType.DELETED else new_mount
    path = os.path.join(mount, node.full_path)
    try:
        node.size_bytes = os.stat(path).st_size
    except OSError:
        node.size_bytes = 0
