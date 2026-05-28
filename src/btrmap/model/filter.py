"""Filter a :class:`~btrmap.model.diff_tree.DiffNode` tree by change type, path, and size.

The public API is :func:`apply_filter` and :func:`count_leaves`.  :class:`FilterSpec`
is an immutable snapshot of the current filter state; :meth:`FilterSpec.is_identity`
fast-paths the common case where no filter is active, avoiding any tree allocation.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from btrmap.btrfs.diff import ChangeType
from btrmap.model.diff_tree import DiffNode

if TYPE_CHECKING:
    pass  # no extra imports needed


@dataclass(frozen=True)
class FilterSpec:
    """Immutable snapshot of the current filter state.

    ``enabled_types``: leaves whose ``change_type`` is not in this set are pruned.
    ``hide_dotfiles``: prunes any node (file or directory) whose name starts with ``'.'``.
    ``min_size_bytes``: prunes leaves smaller than this threshold (0 = disabled).
    ``path_search``: case-insensitive substring match against ``full_path`` ("" = disabled).

    Note: ``ChangeType.RENAMED`` never appears on leaves because :meth:`DiffTree.build`
    always expands RENAMED records into DELETED + CREATED pairs.  The checkbox is kept
    in the UI for completeness but has no visible effect on a built tree.
    """

    enabled_types: frozenset[ChangeType] = field(
        default_factory=lambda: frozenset(ChangeType)
    )
    hide_dotfiles: bool = False
    min_size_bytes: int = 0
    path_search: str = ""

    @classmethod
    def default(cls) -> FilterSpec:
        return cls()

    def is_identity(self) -> bool:
        """True when the spec would pass every node unchanged — fast-path guard."""
        return (
            self.enabled_types == frozenset(ChangeType)
            and not self.hide_dotfiles
            and self.min_size_bytes == 0
            and self.path_search == ""
        )


# ── Filtering logic ───────────────────────────────────────────────────────────


def _keep_leaf(node: DiffNode, spec: FilterSpec) -> bool:
    if node.change_type not in spec.enabled_types:
        return False
    if spec.hide_dotfiles and node.name.startswith("."):
        return False
    if spec.min_size_bytes > 0 and node.size_bytes < spec.min_size_bytes:
        return False
    if spec.path_search and spec.path_search.lower() not in node.full_path.lower():
        return False
    return True


def _prune(node: DiffNode, spec: FilterSpec) -> DiffNode | None:
    """Recursively prune nodes that don't match spec.

    The root node (name == "") is never pruned even if all its children
    are removed — the caller always receives a valid (possibly childless) root.
    """
    is_root = node.name == ""

    # Dotfile pruning — prune the entire subtree of any non-root dotfile node.
    if not is_root and spec.hide_dotfiles and node.name.startswith("."):
        return None

    if node.is_leaf:
        return node if _keep_leaf(node, spec) else None

    # Recurse into children.
    new_children: dict[str, DiffNode] = {}
    for name, child in node.children.items():
        result = _prune(child, spec)
        if result is not None:
            new_children[name] = result

    # Prune non-root directory that became empty after child pruning.
    if not new_children and not is_root:
        return None

    return replace(node, children=new_children)


def apply_filter(root: DiffNode, spec: FilterSpec) -> DiffNode:
    """Return a new DiffNode subtree containing only nodes that pass spec.

    Leaf DiffNode objects are shared (not copied) between the original tree
    and the result.  Interior nodes are shallow-copied with a pruned children
    dict.  The root is always returned, even if all children are filtered out.
    """
    if spec.is_identity():
        return root  # avoid any allocation when no filter is active
    result = _prune(root, spec)
    assert result is not None, "root must never be pruned"
    return result


def count_leaves(node: DiffNode) -> int:
    """Return the number of real leaf descendants (nodes with a non-empty full_path).

    The virtual root node (full_path == "") is excluded even when it has no
    children, because an empty root represents a diff with zero results, not a
    single file change.
    """
    if node.is_leaf:
        return 1 if node.full_path else 0  # exclude the virtual root
    return sum(count_leaves(child) for child in node.children.values())
