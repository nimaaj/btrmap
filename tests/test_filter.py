"""Tests for model/filter.py — pure Python, no Qt, no btrfs required."""
from __future__ import annotations

from btrmap.btrfs.diff import ChangeRecord, ChangeType
from btrmap.model.diff_tree import DiffTree
from btrmap.model.filter import FilterSpec, apply_filter, count_leaves

# ── Helpers ───────────────────────────────────────────────────────────────────


def _tree(*records: ChangeRecord) -> DiffTree:
    tree = DiffTree.build(list(records))
    # Give every leaf a nonzero size so min_size tests work predictably
    for leaf in tree.iter_leaves():
        if leaf.size_bytes == 0:
            leaf.size_bytes = 1_000
    return tree


def _paths(root) -> set[str]:
    """Collect full_path of every real leaf reachable from root.

    The virtual root (full_path == "") is excluded even when it is leaf-like
    (no children), matching the semantics of count_leaves().
    """
    from btrmap.model.diff_tree import DiffNode

    def walk(node: DiffNode) -> set[str]:
        if node.is_leaf:
            return {node.full_path} if node.full_path else set()
        result: set[str] = set()
        for child in node.children.values():
            result |= walk(child)
        return result

    return walk(root)


# ── FilterSpec helpers ────────────────────────────────────────────────────────


def test_filter_spec_default_is_identity() -> None:
    assert FilterSpec.default().is_identity()


def test_filter_spec_without_one_type_is_not_identity() -> None:
    types = frozenset(ChangeType) - {ChangeType.DELETED}
    spec = FilterSpec(enabled_types=types)
    assert not spec.is_identity()


def test_filter_spec_hide_dotfiles_is_not_identity() -> None:
    assert not FilterSpec(hide_dotfiles=True).is_identity()


def test_filter_spec_min_size_is_not_identity() -> None:
    assert not FilterSpec(min_size_bytes=1024).is_identity()


def test_filter_spec_path_search_is_not_identity() -> None:
    assert not FilterSpec(path_search="foo").is_identity()


# ── Identity fast-path ────────────────────────────────────────────────────────


def test_identity_returns_exact_same_object() -> None:
    tree = _tree(ChangeRecord(ChangeType.CREATED, "usr/bin/find"))
    result = apply_filter(tree.root, FilterSpec.default())
    assert result is tree.root


# ── Change-type toggle ────────────────────────────────────────────────────────


def test_type_toggle_hides_leaf() -> None:
    tree = _tree(
        ChangeRecord(ChangeType.CREATED, "usr/new.py"),
        ChangeRecord(ChangeType.DELETED, "usr/old.py"),
    )
    spec = FilterSpec(enabled_types=frozenset(ChangeType) - {ChangeType.DELETED})
    filtered = apply_filter(tree.root, spec)
    paths = _paths(filtered)
    assert "usr/new.py" in paths
    assert "usr/old.py" not in paths


def test_type_toggle_preserves_sibling() -> None:
    tree = _tree(
        ChangeRecord(ChangeType.CREATED, "etc/new.conf"),
        ChangeRecord(ChangeType.PERMISSIONS, "etc/old.conf"),
    )
    spec = FilterSpec(enabled_types=frozenset({ChangeType.CREATED}))
    filtered = apply_filter(tree.root, spec)
    paths = _paths(filtered)
    assert "etc/new.conf" in paths
    assert "etc/old.conf" not in paths


def test_empty_dir_pruned_when_all_children_filtered() -> None:
    tree = _tree(
        ChangeRecord(ChangeType.DELETED, "var/log/old.log"),
        ChangeRecord(ChangeType.CREATED, "usr/bin/find"),
    )
    spec = FilterSpec(enabled_types=frozenset(ChangeType) - {ChangeType.DELETED})
    filtered = apply_filter(tree.root, spec)
    # var/log dir must be gone; usr/bin/find must remain
    assert "var" not in filtered.children
    assert "usr" in filtered.children


# ── Root is never pruned ──────────────────────────────────────────────────────


def test_root_never_pruned_even_when_all_children_gone() -> None:
    tree = _tree(ChangeRecord(ChangeType.CREATED, "usr/lib/foo.so"))
    spec = FilterSpec(enabled_types=frozenset())  # hide everything
    filtered = apply_filter(tree.root, spec)
    # root must still be returned (not None), just with no children
    assert filtered is not None
    assert filtered.name == ""
    assert len(filtered.children) == 0


def test_root_not_treated_as_dotfile() -> None:
    # root.name == "" — must not be pruned even though "" doesn't start with "."
    # (this is technically trivially safe, but the invariant should be explicit)
    tree = _tree(ChangeRecord(ChangeType.MODIFIED, "usr/lib/foo.so"))
    spec = FilterSpec(hide_dotfiles=True)
    filtered = apply_filter(tree.root, spec)
    assert filtered is not None
    assert "usr" in filtered.children


# ── Dotfile filtering ─────────────────────────────────────────────────────────


def test_hide_dotfiles_prunes_dotfile_leaf() -> None:
    tree = _tree(
        ChangeRecord(ChangeType.PERMISSIONS, "home/user/.bashrc"),
        ChangeRecord(ChangeType.MODIFIED, "home/user/notes.txt"),
    )
    spec = FilterSpec(hide_dotfiles=True)
    filtered = apply_filter(tree.root, spec)
    paths = _paths(filtered)
    assert "home/user/.bashrc" not in paths
    assert "home/user/notes.txt" in paths


def test_hide_dotfiles_prunes_entire_dotdir_subtree() -> None:
    tree = _tree(
        ChangeRecord(ChangeType.PERMISSIONS, "home/user/.config/app/settings.json"),
        ChangeRecord(ChangeType.PERMISSIONS, "home/user/.config/app/cache.db"),
        ChangeRecord(ChangeType.CREATED, "home/user/report.pdf"),
    )
    spec = FilterSpec(hide_dotfiles=True)
    filtered = apply_filter(tree.root, spec)
    paths = _paths(filtered)
    assert not any(".config" in p for p in paths)
    assert "home/user/report.pdf" in paths


def test_hide_dotfiles_does_not_affect_normal_files() -> None:
    tree = _tree(ChangeRecord(ChangeType.MODIFIED, "usr/bin/find"))
    spec = FilterSpec(hide_dotfiles=True)
    filtered = apply_filter(tree.root, spec)
    assert "usr/bin/find" in _paths(filtered)


# ── Minimum size filtering ────────────────────────────────────────────────────


def test_min_size_hides_small_leaf() -> None:
    tree = _tree(
        ChangeRecord(ChangeType.MODIFIED, "usr/lib/big.so"),
        ChangeRecord(ChangeType.MODIFIED, "etc/tiny.conf"),
    )
    # Manually adjust sizes
    for leaf in tree.iter_leaves():
        leaf.size_bytes = 10_000_000 if "big" in leaf.name else 100

    spec = FilterSpec(min_size_bytes=1024)
    filtered = apply_filter(tree.root, spec)
    paths = _paths(filtered)
    assert "usr/lib/big.so" in paths
    assert "etc/tiny.conf" not in paths


def test_min_size_zero_is_noop() -> None:
    tree = _tree(ChangeRecord(ChangeType.CREATED, "etc/tiny.conf"))
    for leaf in tree.iter_leaves():
        leaf.size_bytes = 1  # very small
    spec = FilterSpec(min_size_bytes=0)
    filtered = apply_filter(tree.root, spec)
    assert "etc/tiny.conf" in _paths(filtered)


def test_min_size_boundary_inclusive() -> None:
    """A leaf exactly at the threshold must be kept."""
    tree = _tree(ChangeRecord(ChangeType.CREATED, "etc/exact.conf"))
    for leaf in tree.iter_leaves():
        leaf.size_bytes = 1024
    spec = FilterSpec(min_size_bytes=1024)
    filtered = apply_filter(tree.root, spec)
    assert "etc/exact.conf" in _paths(filtered)


# ── Path search filtering ─────────────────────────────────────────────────────


def test_path_search_case_insensitive() -> None:
    tree = _tree(
        ChangeRecord(ChangeType.MODIFIED, "usr/lib/python3.13/asyncio/__init__.py"),
        ChangeRecord(ChangeType.MODIFIED, "usr/lib/firefox/libxul.so"),
    )
    spec = FilterSpec(path_search="PYTHON")
    filtered = apply_filter(tree.root, spec)
    paths = _paths(filtered)
    assert any("python" in p for p in paths)
    assert not any("firefox" in p for p in paths)


def test_path_search_empty_is_noop() -> None:
    tree = _tree(ChangeRecord(ChangeType.CREATED, "usr/bin/find"))
    spec = FilterSpec(path_search="")
    filtered = apply_filter(tree.root, spec)
    assert "usr/bin/find" in _paths(filtered)


def test_path_search_no_match_prunes_all() -> None:
    tree = _tree(ChangeRecord(ChangeType.CREATED, "usr/bin/find"))
    spec = FilterSpec(path_search="zzznomatch")
    filtered = apply_filter(tree.root, spec)
    assert len(_paths(filtered)) == 0


# ── Combined filters ──────────────────────────────────────────────────────────


def test_combined_filters_are_conjunctive() -> None:
    """A leaf must pass ALL active filters to survive."""
    tree = _tree(
        ChangeRecord(ChangeType.CREATED, "usr/lib/python3.13/big_module.so"),
        ChangeRecord(ChangeType.CREATED, "usr/lib/python3.13/.hidden.so"),
        ChangeRecord(ChangeType.CREATED, "usr/lib/firefox/libxul.so"),
        ChangeRecord(ChangeType.DELETED, "usr/lib/python3.12/old.py"),
    )
    for leaf in tree.iter_leaves():
        leaf.size_bytes = 5_000_000 if "big" in leaf.name or "libxul" in leaf.name else 100

    spec = FilterSpec(
        enabled_types=frozenset({ChangeType.CREATED}),
        hide_dotfiles=True,
        min_size_bytes=1_000_000,
        path_search="python",
    )
    filtered = apply_filter(tree.root, spec)
    paths = _paths(filtered)
    # Only "usr/lib/python3.13/big_module.so" satisfies all 4 conditions
    assert paths == {"usr/lib/python3.13/big_module.so"}


# ── count_leaves ─────────────────────────────────────────────────────────────


def test_count_leaves_empty_root() -> None:
    from btrmap.model.diff_tree import DiffNode

    empty_root = DiffNode(name="", full_path="", change_type=None, size_bytes=0)
    assert count_leaves(empty_root) == 0


def test_count_leaves_matches_iter_leaves() -> None:
    tree = _tree(
        ChangeRecord(ChangeType.CREATED, "usr/bin/find"),
        ChangeRecord(ChangeType.DELETED, "etc/old.conf"),
        ChangeRecord(ChangeType.MODIFIED, "var/log/app.log"),
    )
    assert count_leaves(tree.root) == sum(1 for _ in tree.iter_leaves())


def test_count_leaves_after_filter() -> None:
    tree = _tree(
        ChangeRecord(ChangeType.CREATED, "a/b/c.txt"),
        ChangeRecord(ChangeType.DELETED, "a/b/d.txt"),
        ChangeRecord(ChangeType.DELETED, "x/y.txt"),
    )
    spec = FilterSpec(enabled_types=frozenset({ChangeType.CREATED}))
    filtered = apply_filter(tree.root, spec)
    assert count_leaves(filtered) == 1
