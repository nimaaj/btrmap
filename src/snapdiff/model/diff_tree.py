# src/snapdiff/model/diff_tree.py
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from snapdiff.btrfs.diff import ChangeRecord, ChangeType


@dataclass
class DiffNode:
    name: str
    full_path: str
    change_type: ChangeType | None
    size_bytes: int
    children: dict[str, "DiffNode"] = field(default_factory=dict)

    @property
    def total_size(self) -> int:
        """Recursive sum of size_bytes across all leaf descendants."""
        if self.is_leaf:
            return self.size_bytes
        return sum(child.total_size for child in self.children.values())

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0


@dataclass
class DiffTree:
    root: DiffNode

    @classmethod
    def build(cls, records: list[ChangeRecord]) -> "DiffTree":
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
