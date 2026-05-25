# src/snapdiff/ui/main_window.py
from __future__ import annotations

from PyQt6.QtCore import QSettings, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from snapdiff.btrfs.diff import compute_diff
from snapdiff.model.diff_tree import DiffTree
from snapdiff.model.enrichment import enrich
from snapdiff.ui.snapshot_selector import SnapshotSelector
from snapdiff.ui.tree_view import DiffTreeView, _fmt_size
from snapdiff.ui.treemap import TreemapWidget


class DiffWorker(QThread):
    finished: pyqtSignal = pyqtSignal(object)  # emits DiffTree
    error: pyqtSignal = pyqtSignal(Exception)

    def __init__(self, base_path: str, new_path: str, parent=None) -> None:
        super().__init__(parent)
        self._base = base_path
        self._new = new_path

    def run(self) -> None:
        try:
            records = compute_diff(self._base, self._new)
            tree = DiffTree.build(records)
            enrich(tree, self._new, self._base)
            self.finished.emit(tree)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(exc)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("btrfs-snapdiff")
        self.resize(1200, 800)

        self._syncing = False
        self._worker: DiffWorker | None = None

        # Widgets
        self._selector = SnapshotSelector()
        self._tree_view = DiffTreeView()
        self._treemap = TreemapWidget()

        # Layout
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._tree_view)
        self._splitter.addWidget(self._treemap)
        self._splitter.setSizes([400, 800])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._selector)
        layout.addWidget(self._splitter, stretch=1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready — load snapshots and click Compare.")

        # Restore splitter state
        settings = QSettings("btrfs-snapdiff", "main")
        if settings.contains("splitter"):
            self._splitter.restoreState(settings.value("splitter"))  # type: ignore[arg-type]

        # Wire signals
        self._selector.diff_requested.connect(self._start_diff)
        self._tree_view.node_selected.connect(self._on_node_selected)
        self._treemap.node_selected.connect(self._on_node_selected)

    def _start_diff(self, base_path: str, new_path: str) -> None:
        if self._worker and self._worker.isRunning():
            self.statusBar().showMessage("A diff is already running…")
            return
        self.statusBar().showMessage(f"Comparing {base_path} → {new_path}…")
        self._worker = DiffWorker(base_path, new_path, self)
        self._worker.finished.connect(self._on_diff_finished)
        self._worker.error.connect(self._on_diff_error)
        self._worker.start()

    def _on_diff_finished(self, tree: DiffTree) -> None:
        self._tree_view.set_tree(tree)
        self._treemap.set_root(tree.root)
        n_leaves = sum(1 for _ in tree.iter_leaves())
        self.statusBar().showMessage(
            f"{n_leaves} change(s) · {_fmt_size(tree.root.total_size)} total"
        )

    def _on_diff_error(self, exc: Exception) -> None:
        msg = str(exc)
        self.statusBar().showMessage(f"Error: {msg}")
        QMessageBox.critical(self, "Diff failed", msg)

    def _on_node_selected(self, full_path: str) -> None:
        if self._syncing:
            return
        self._syncing = True
        self._tree_view.select_node(full_path)
        self._treemap.select_node(full_path)
        self._syncing = False

    def closeEvent(self, event) -> None:  # type: ignore[override]
        settings = QSettings("btrfs-snapdiff", "main")
        settings.setValue("splitter", self._splitter.saveState())
        super().closeEvent(event)
