# tests/test_diff_tree.py
from __future__ import annotations

import pytest

from snapdiff.btrfs.diff import ChangeRecord, ChangeType
from snapdiff.model.diff_tree import DiffNode, DiffTree


def _records(*args: tuple[ChangeType, str, str | None]) -> list[ChangeRecord]:
    return [ChangeRecord(ct, path, old_path) for ct, path, old_path in args]


def test_build_single_leaf() -> None:
    records = [ChangeRecord(ChangeType.CREATED, "file.txt")]
    tree = DiffTree.build(records)
    node = tree.find("file.txt")
    assert node is not None
    assert node.name == "file.txt"
    assert node.change_type == ChangeType.CREATED
    assert node.is_leaf


def test_build_nested_path_creates_intermediate_nodes() -> None:
    records = [ChangeRecord(ChangeType.MODIFIED, "src/main.py")]
    tree = DiffTree.build(records)
    src = tree.find("src")
    assert src is not None
    assert src.change_type is None  # intermediate dir
    assert not src.is_leaf
    main_py = tree.find("src/main.py")
    assert main_py is not None
    assert main_py.change_type == ChangeType.MODIFIED
    assert main_py.is_leaf


def test_build_renamed_expands_to_two_leaves() -> None:
    records = [ChangeRecord(ChangeType.RENAMED, "new.txt", old_path="old.txt")]
    tree = DiffTree.build(records)
    old_node = tree.find("old.txt")
    new_node = tree.find("new.txt")
    assert old_node is not None
    assert old_node.change_type == ChangeType.DELETED
    assert new_node is not None
    assert new_node.change_type == ChangeType.CREATED


def test_total_size_leaf() -> None:
    records = [ChangeRecord(ChangeType.CREATED, "a.txt")]
    tree = DiffTree.build(records)
    node = tree.find("a.txt")
    assert node is not None
    node.size_bytes = 1024
    assert node.total_size == 1024


def test_total_size_aggregates_across_children() -> None:
    records = [
        ChangeRecord(ChangeType.CREATED, "dir/a.txt"),
        ChangeRecord(ChangeType.MODIFIED, "dir/b.txt"),
    ]
    tree = DiffTree.build(records)
    a = tree.find("dir/a.txt")
    b = tree.find("dir/b.txt")
    a.size_bytes = 100
    b.size_bytes = 200
    dir_node = tree.find("dir")
    assert dir_node.total_size == 300


def test_iter_leaves_returns_only_leaves() -> None:
    records = [
        ChangeRecord(ChangeType.CREATED, "dir/a.txt"),
        ChangeRecord(ChangeType.MODIFIED, "dir/b.txt"),
        ChangeRecord(ChangeType.DELETED, "root_file.txt"),
    ]
    tree = DiffTree.build(records)
    leaves = list(tree.iter_leaves())
    assert len(leaves) == 3
    assert all(n.is_leaf for n in leaves)
    paths = {n.full_path for n in leaves}
    assert paths == {"dir/a.txt", "dir/b.txt", "root_file.txt"}


def test_find_returns_none_for_missing_path() -> None:
    tree = DiffTree.build([ChangeRecord(ChangeType.CREATED, "a.txt")])
    assert tree.find("nonexistent.txt") is None


def test_find_root() -> None:
    tree = DiffTree.build([ChangeRecord(ChangeType.CREATED, "a.txt")])
    root = tree.find("")
    assert root is tree.root
