"""Tests for the enrichment layer: correct snapshot mount selection and silent stat-failure handling."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from btrmap.btrfs.diff import ChangeRecord, ChangeType
from btrmap.model.diff_tree import DiffTree
from btrmap.model.enrichment import enrich


def _make_tree(*records: ChangeRecord) -> DiffTree:
    return DiffTree.build(list(records))


def _stat(size: int) -> MagicMock:
    m = MagicMock()
    m.st_size = size
    return m


def test_enrich_created_uses_new_snapshot() -> None:
    tree = _make_tree(ChangeRecord(ChangeType.CREATED, "new_file.txt"))
    with patch("os.stat", return_value=_stat(1024)) as mock_stat:
        enrich(tree, "/new", "/base")
        mock_stat.assert_called_once_with("/new/new_file.txt")
    assert tree.find("new_file.txt").size_bytes == 1024


def test_enrich_deleted_uses_base_snapshot() -> None:
    tree = _make_tree(ChangeRecord(ChangeType.DELETED, "old_file.txt"))
    with patch("os.stat", return_value=_stat(512)) as mock_stat:
        enrich(tree, "/new", "/base")
        mock_stat.assert_called_once_with("/base/old_file.txt")
    assert tree.find("old_file.txt").size_bytes == 512


def test_enrich_modified_uses_new_snapshot() -> None:
    tree = _make_tree(ChangeRecord(ChangeType.MODIFIED, "changed.txt"))
    with patch("os.stat", return_value=_stat(2048)) as mock_stat:
        enrich(tree, "/new", "/base")
        mock_stat.assert_called_once_with("/new/changed.txt")
    assert tree.find("changed.txt").size_bytes == 2048


def test_enrich_renamed_uses_new_snapshot_for_new_path() -> None:
    # RENAMED expands to DELETED (old) + CREATED (new)
    tree = _make_tree(ChangeRecord(ChangeType.RENAMED, "b.txt", old_path="a.txt"))
    sizes = {"a.txt": 100, "b.txt": 200}

    def fake_stat(path: str) -> MagicMock:
        for name, size in sizes.items():
            if path.endswith(name):
                return _stat(size)
        raise OSError("not found")

    with patch("os.stat", side_effect=fake_stat):
        enrich(tree, "/new", "/base")

    assert tree.find("a.txt").size_bytes == 100  # DELETED → base
    assert tree.find("b.txt").size_bytes == 200  # CREATED → new


def test_enrich_stat_failure_is_silent() -> None:
    tree = _make_tree(ChangeRecord(ChangeType.CREATED, "missing.sock"))
    with patch("os.stat", side_effect=OSError("not a regular file")):
        enrich(tree, "/new", "/base")  # must not raise
    assert tree.find("missing.sock").size_bytes == 0
