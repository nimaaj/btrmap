"""Collapsible file-tree view of a snapshot diff, built on QAbstractItemModel.

:class:`DiffTreeModel` stores DiffNode pointers directly inside QModelIndex via
``internalPointer``.  :class:`DiffTreeView` wraps it with column resizing and
a ``node_selected`` signal for cross-widget synchronisation.
"""
from __future__ import annotations

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHeaderView, QTreeView

from btrmap.btrfs.diff import ChangeType
from btrmap.model.diff_tree import DiffNode, DiffTree

_CHANGE_COLORS: dict[ChangeType, QColor] = {
    ChangeType.CREATED: QColor("#4caf50"),
    ChangeType.MODIFIED: QColor("#ff9800"),
    ChangeType.DELETED: QColor("#f44336"),
    ChangeType.RENAMED: QColor("#2196f3"),
    ChangeType.PERMISSIONS: QColor("#9e9e9e"),
}


def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size} {unit}"
        size //= 1024
    return f"{size} TB"


class DiffTreeModel(QAbstractItemModel):
    """Qt item model that exposes a :class:`~btrmap.model.diff_tree.DiffTree`.

    Node lookup is O(1): ``_parent_map`` and ``_row_map`` are built once at
    construction and keyed by ``full_path``.  :py:meth:`index` stores the
    ``DiffNode`` pointer in the ``QModelIndex`` so ``data()`` and ``parent()``
    never need to re-traverse the tree.
    """

    HEADERS = ["Name", "Change", "Size"]

    def __init__(self, tree: DiffTree, parent=None) -> None:
        super().__init__(parent)
        self._tree = tree
        self._parent_map: dict[str, DiffNode | None] = {}  # full_path → parent node
        self._row_map: dict[str, int] = {}                  # full_path → row within parent
        self._build_maps(tree.root, None, 0)

    def _build_maps(self, node: DiffNode, parent: DiffNode | None, row: int) -> None:
        self._parent_map[node.full_path] = parent
        self._row_map[node.full_path] = row
        for i, child in enumerate(node.children.values()):
            self._build_maps(child, node, i)

    def _node(self, index: QModelIndex) -> DiffNode:
        if not index.isValid():
            return self._tree.root
        return index.internalPointer()  # type: ignore[return-value]

    # ── QAbstractItemModel interface ──────────────────────────────────────────

    def index(self, row: int, col: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:  # noqa: B008
        parent_node = self._node(parent)
        children = list(parent_node.children.values())
        if 0 <= row < len(children):
            return self.createIndex(row, col, children[row])
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:  # type: ignore[override]
        if not index.isValid():
            return QModelIndex()
        node: DiffNode = index.internalPointer()  # type: ignore[assignment]
        parent_node = self._parent_map.get(node.full_path)
        if parent_node is None:
            return QModelIndex()
        row = self._row_map.get(parent_node.full_path, 0)
        return self.createIndex(row, 0, parent_node)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return len(self._node(parent).children)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 3

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node: DiffNode = index.internalPointer()  # type: ignore[assignment]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return node.name
            if col == 1:
                return node.change_type.value if node.change_type else ""
            if col == 2:
                if node.is_leaf:
                    return _fmt_size(node.size_bytes)
                return f"({_fmt_size(node.total_size)} total)"

        if role == Qt.ItemDataRole.ForegroundRole:
            if node.change_type is not None:
                return _CHANGE_COLORS.get(node.change_type)

        return None

    # ── Path lookup ───────────────────────────────────────────────────────────

    def index_for_path(self, full_path: str) -> QModelIndex:
        node = self._tree.find(full_path)
        if node is None or node is self._tree.root:
            return QModelIndex()
        row = self._row_map.get(node.full_path, 0)
        return self.createIndex(row, 0, node)


class DiffTreeView(QTreeView):
    """QTreeView pre-configured for diff trees with cross-widget selection sync."""

    node_selected = pyqtSignal(str)  # emits full_path

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        hdr = self.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)  # faster paint for large trees

    def set_tree(self, tree: DiffTree) -> None:
        model = DiffTreeModel(tree, self)
        self.setModel(model)
        self.selectionModel().selectionChanged.connect(self._on_selection)
        self.expandToDepth(1)

    def _on_selection(self) -> None:
        indexes = self.selectedIndexes()
        if indexes:
            node: DiffNode = indexes[0].internalPointer()  # type: ignore[assignment]
            self.node_selected.emit(node.full_path)

    def select_node(self, full_path: str) -> None:
        """Select node programmatically (called during cross-widget sync)."""
        model = self.model()
        if not isinstance(model, DiffTreeModel):
            return
        idx = model.index_for_path(full_path)
        if idx.isValid():
            self.blockSignals(True)
            self.setCurrentIndex(idx)
            self.scrollTo(idx)
            self.blockSignals(False)
