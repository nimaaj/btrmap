"""Top-level application window: wires all widgets together and manages the diff lifecycle.

The diff runs in a background :class:`DiffWorker` (QThread) so the UI stays responsive
during the btrfs send/receive pipeline.  Filter changes are applied synchronously in the
main thread because :func:`~btrmap.model.filter.apply_filter` is fast enough not to
require a worker.
"""
from __future__ import annotations

from PyQt6.QtCore import QSettings, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from btrmap.btrfs.diff import compute_diff
from btrmap.model.diff_tree import DiffTree
from btrmap.model.enrichment import enrich
from btrmap.model.filter import FilterSpec, apply_filter, count_leaves
from btrmap.ui.filter_panel import FilterPanel
from btrmap.ui.snapshot_selector import SnapshotSelector
from btrmap.ui.tree_view import DiffTreeView, _fmt_size
from btrmap.ui.treemap import TreemapWidget


class DiffWorker(QThread):
    """Background thread that runs the three-phase diff pipeline.

    Phases: (1) ``btrfs send | btrfs receive --dump`` → raw records,
    (2) :meth:`~btrmap.model.diff_tree.DiffTree.build` → tree,
    (3) :func:`~btrmap.model.enrichment.enrich` → file sizes.
    Progress messages are emitted after each phase and periodically within phases.
    """

    finished: pyqtSignal = pyqtSignal(object)  # emits DiffTree
    error: pyqtSignal = pyqtSignal(Exception)
    progress: pyqtSignal = pyqtSignal(str)  # human-readable status message

    def __init__(self, base_path: str, new_path: str, parent=None) -> None:
        super().__init__(parent)
        self._base = base_path
        self._new = new_path

    def run(self) -> None:
        try:
            self.progress.emit("Step 1/3  Running btrfs diff…")

            def diff_progress(n: int) -> None:
                self.progress.emit(f"Step 1/3  Scanning… {n:,} changes found")

            records = compute_diff(self._base, self._new, progress_cb=diff_progress)

            self.progress.emit(f"Step 2/3  Building tree ({len(records):,} changes)…")
            tree = DiffTree.build(records)

            n_leaves = sum(1 for _ in tree.iter_leaves())
            self.progress.emit(f"Step 3/3  Measuring sizes ({n_leaves:,} files)…")

            def enrich_progress(n: int) -> None:
                self.progress.emit(f"Step 3/3  Measuring sizes… {n:,}/{n_leaves:,}")

            enrich(tree, self._new, self._base, progress_cb=enrich_progress)
            self.finished.emit(tree)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(exc)


class MainWindow(QMainWindow):
    """Top-level window: snapshot selector → filter bar → tree view + treemap."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("btrmap")
        self.resize(1200, 800)

        self._syncing = False
        self._worker: DiffWorker | None = None
        self._current_tree: DiffTree | None = None  # unfiltered tree from last diff

        # Widgets
        self._selector = SnapshotSelector()
        self._filter_panel = FilterPanel()
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
        layout.setSpacing(4)
        layout.addWidget(self._selector)
        layout.addWidget(self._filter_panel)   # filter bar between selector and views
        layout.addWidget(self._splitter, stretch=1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready — load snapshots and click Compare.")

        # Busy indicator (indeterminate progress bar in the status bar)
        self._busy_bar = QProgressBar()
        self._busy_bar.setRange(0, 0)  # indeterminate / bouncing
        self._busy_bar.setFixedWidth(160)
        self._busy_bar.setFixedHeight(16)
        self._busy_bar.setTextVisible(False)
        self.statusBar().addPermanentWidget(self._busy_bar)
        self._busy_bar.hide()

        # Restore splitter state
        settings = QSettings("btrmap", "main")
        if settings.contains("splitter"):
            self._splitter.restoreState(settings.value("splitter"))  # type: ignore[arg-type]

        # Ctrl+F → focus path search; Ctrl+R → reset all filters
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(
            self._filter_panel.focus_search
        )
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(
            self._filter_panel.reset
        )

        # Wire signals
        self._selector.diff_requested.connect(self._start_diff)
        self._filter_panel.filter_changed.connect(self._on_filter_changed)
        self._tree_view.node_selected.connect(self._on_node_selected)
        self._treemap.node_selected.connect(self._on_node_selected)

    # ── Diff lifecycle ────────────────────────────────────────────────────────

    def _start_diff(self, base_path: str, new_path: str) -> None:
        if self._worker and self._worker.isRunning():
            self.statusBar().showMessage("A diff is already running…")
            return
        self.statusBar().showMessage(f"Comparing {base_path} → {new_path}…")
        self._worker = DiffWorker(base_path, new_path, self)
        self._worker.finished.connect(self._on_diff_finished)
        self._worker.error.connect(self._on_diff_error)
        self._worker.progress.connect(self.statusBar().showMessage)
        self._busy_bar.show()
        self._worker.start()

    def _on_diff_finished(self, tree: DiffTree) -> None:
        self._busy_bar.hide()
        self._current_tree = tree
        self._filter_panel.set_enabled(True)
        # reset() fires filter_changed → _on_filter_changed → _apply_and_display
        self._filter_panel.reset()

    def _on_diff_error(self, exc: Exception) -> None:
        self._busy_bar.hide()
        msg = str(exc)
        self.statusBar().showMessage(f"Error: {msg}")
        QMessageBox.critical(self, "Diff failed", msg)

    # ── Filtering ─────────────────────────────────────────────────────────────

    def _on_filter_changed(self, spec: FilterSpec) -> None:
        if self._current_tree is not None:
            self._apply_and_display()

    def _apply_and_display(self) -> None:
        assert self._current_tree is not None
        spec = self._filter_panel.current_spec()
        filtered = apply_filter(self._current_tree.root, spec)
        total = sum(1 for _ in self._current_tree.iter_leaves())
        shown = count_leaves(filtered)
        # Wrap filtered DiffNode in a DiffTree so set_tree() works unchanged.
        # DiffTree is a plain @dataclass — wrapping a filtered root is always safe.
        self._tree_view.set_tree(DiffTree(root=filtered))
        self._treemap.set_root(filtered)
        self.statusBar().showMessage(
            f"{shown} of {total} change(s) shown · {_fmt_size(filtered.total_size)} total"
        )

    # ── Cross-widget node selection ───────────────────────────────────────────

    def _on_node_selected(self, full_path: str) -> None:
        if self._syncing:
            return
        self._syncing = True
        self._tree_view.select_node(full_path)
        self._treemap.select_node(full_path)
        self._syncing = False

    def closeEvent(self, event) -> None:  # type: ignore[override]
        settings = QSettings("btrmap", "main")
        settings.setValue("splitter", self._splitter.saveState())
        super().closeEvent(event)
