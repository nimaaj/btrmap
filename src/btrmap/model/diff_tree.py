"""In-memory tree representation of a btrfs snapshot diff.

:class:`DiffTree` is built once from a list of :class:`~btrmap.btrfs.diff.ChangeRecord`
objects and then treated as read-only.  Directory nodes carry ``change_type=None``; leaf
nodes carry the actual :class:`~btrmap.btrfs.diff.ChangeType`.  RENAMED records are
expanded into a DELETED leaf for the old path and a CREATED leaf for the new path.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from btrmap.btrfs.diff import ChangeRecord, ChangeType


@dataclass
class DiffNode:
    """A node in the diff tree — either an intermediate directory or a changed file leaf.

    Leaf nodes (``is_leaf == True``) have a non-``None`` ``change_type`` and a meaningful
    ``full_path``.  Directory nodes have ``change_type=None`` and aggregate sizes from
    their children via ``total_size``.
    """

    name: str
    full_path: str
    change_type: ChangeType | None
    size_bytes: int
    children: dict[str, DiffNode] = field(default_factory=dict)

    @property
    def total_size(self) -> int:
        """Recursive sum of ``size_bytes`` across all leaf descendants."""
        if self.is_leaf:
            return self.size_bytes
        return sum(child.total_size for child in self.children.values())

    @property
    def is_leaf(self) -> bool:
        """True when this node has no children (i.e. it represents a file, not a directory)."""
        return len(self.children) == 0


@dataclass
class DiffTree:
    """Container for the root :class:`DiffNode` of a built diff tree."""

    root: DiffNode

    @classmethod
    def build(cls, records: list[ChangeRecord]) -> DiffTree:
        """Build a tree from *records*, expanding RENAMED entries into DELETED + CREATED pairs."""
        root = DiffNode(name="", full_path="", change_type=None, size_bytes=0)
        for record in records:
            if record.change_type == ChangeType.RENAMED:
                if record.old_path:
                    cls._insert(root, ChangeRecord(ChangeType.DELETED, record.old_path))
                cls._insert(root, ChangeRecord(ChangeType.CREATED, record.path))
            else:
                cls._insert(root, record)
        return cls(root=root)

    @staticmethod
    def _insert(root: DiffNode, record: ChangeRecord) -> None:
        parts = record.path.split("/")
        node = root
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            full_path = "/".join(parts[: i + 1])
            if part not in node.children:
                node.children[part] = DiffNode(
                    name=part,
                    full_path=full_path,
                    change_type=record.change_type if is_last else None,
                    size_bytes=0,
                )
            node = node.children[part]

    def iter_leaves(self) -> Iterator[DiffNode]:
        def _iter(node: DiffNode) -> Iterator[DiffNode]:
            if node.is_leaf:
                yield node
            else:
                for child in node.children.values():
                    yield from _iter(child)

        return _iter(self.root)

    def find(self, full_path: str) -> DiffNode | None:
        if not full_path:
            return self.root
        node = self.root
        for part in full_path.split("/"):
            if part not in node.children:
                return None
            node = node.children[part]
        return node
