"""Snapshot selection widget: filesystem root input and single-list dual-highlight picker.

The user clicks one snapshot to mark it as the *base* and a second to mark it as
*new*; the widget enforces that the lower-indexed entry is always the base.  Colour
highlights (amber = base, green = new) and text suffixes communicate the current
selection at a glance.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from btrmap.btrfs.subvolumes import Subvolume, SubvolumeListError, list_subvolumes

_BASE_COLOR = QColor("#ff9800")  # amber
_NEW_COLOR = QColor("#4caf50")   # green
_BASE_COLOR.setAlphaF(0.45)
_NEW_COLOR.setAlphaF(0.45)

_SUFFIX_BASE = "  ← base"
_SUFFIX_NEW = "  ← new"


class SnapshotSelector(QWidget):
    """Widget that lets the user pick two btrfs snapshots and trigger a diff.

    Emits :attr:`diff_requested` with absolute paths ``(base, new)`` when the
    user clicks *Compare*.
    """

    diff_requested = pyqtSignal(str, str)  # (base_absolute_path, new_absolute_path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._subvolumes: list[Subvolume] = []
        self._base_row: int | None = None
        self._new_row: int | None = None

        # ── Filesystem root row ───────────────────────────────────────────────
        self._fs_edit = QLineEdit("/")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_fs)
        load_btn = QPushButton("Load snapshots")
        load_btn.clicked.connect(self._load_snapshots)

        fs_row = QHBoxLayout()
        fs_row.addWidget(QLabel("Filesystem root:"))
        fs_row.addWidget(self._fs_edit, stretch=1)
        fs_row.addWidget(browse_btn)
        fs_row.addWidget(load_btn)

        # ── Single snapshot list ──────────────────────────────────────────────
        snap_label = QLabel("Snapshots — click to mark base, click another to mark new:")
        self._snap_list = QListWidget()
        self._snap_list.setFixedHeight(130)
        self._snap_list.setAlternatingRowColors(True)
        self._snap_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._snap_list.itemClicked.connect(self._on_item_clicked)

        # ── Status + Compare button ───────────────────────────────────────────
        self._status = QLabel("Load snapshots to begin.")
        compare_btn = QPushButton("Compare")
        compare_btn.setFixedWidth(100)
        compare_btn.clicked.connect(self._on_compare)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self._status, stretch=1)
        bottom_row.addWidget(compare_btn)

        # ── Assemble ──────────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addLayout(fs_row)
        layout.addWidget(snap_label)
        layout.addWidget(self._snap_list)
        layout.addLayout(bottom_row)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _browse_fs(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select btrfs filesystem root", "/")
        if path:
            self._fs_edit.setText(path)

    def _load_snapshots(self) -> None:
        fs_path = self._fs_edit.text().strip() or "/"
        try:
            self._subvolumes = list_subvolumes(fs_path)
        except (SubvolumeListError, RuntimeError, PermissionError) as exc:
            self._status.setText(f"Error: {exc}")
            return

        # Filter out cross-subvolume paths (e.g. @home/.snapshots/… when
        # fs_root="/").  Use "/home" as root to list @home snapshots instead.
        self._subvolumes = [sv for sv in self._subvolumes if not sv.path.startswith("@")]

        self._snap_list.clear()
        self._base_row = None
        self._new_row = None
        for sv in self._subvolumes:
            self._snap_list.addItem(sv.path)

        n = len(self._subvolumes)
        if n >= 2:
            self._base_row = 0
            self._new_row = 1
        elif n == 1:
            self._base_row = 0

        self._refresh_highlights()
        if not n:
            self._status.setText(
                "No snapshots found.  (Try '/' or '/home' as the filesystem root.)"
            )

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        row = self._snap_list.row(item)

        if row == self._base_row:
            self._base_row = None
        elif row == self._new_row:
            self._new_row = None
        elif self._base_row is None:
            self._base_row = row
        else:
            # Both may or may not be set; clicked row becomes new
            self._new_row = row

        # Enforce lower index = base
        if self._base_row is not None and self._new_row is not None:
            if self._base_row > self._new_row:
                self._base_row, self._new_row = self._new_row, self._base_row

        self._refresh_highlights()

    def _refresh_highlights(self) -> None:
        """Repaint item backgrounds and text suffixes to reflect current selection."""
        n = self._snap_list.count()
        for row in range(n):
            item = self._snap_list.item(row)
            assert item is not None
            # Strip any previous suffix to get the clean path back
            text = item.text()
            for suffix in (_SUFFIX_BASE, _SUFFIX_NEW):
                if text.endswith(suffix):
                    text = text[: -len(suffix)]
                    break
            # Reset to system-default foreground and transparent background
            item.setBackground(QBrush(Qt.GlobalColor.transparent))
            item.setData(Qt.ItemDataRole.ForegroundRole, None)  # clears custom colour

            if row == self._base_row:
                item.setText(text + _SUFFIX_BASE)
                item.setBackground(QBrush(_BASE_COLOR))
            elif row == self._new_row:
                item.setText(text + _SUFFIX_NEW)
                item.setBackground(QBrush(_NEW_COLOR))
            else:
                item.setText(text)

        # Update guidance status
        n_sv = len(self._subvolumes)
        hint = "  (Use '/home' as root for @home snapshots.)" if n_sv else ""
        if n_sv == 0:
            return  # status set in _load_snapshots
        if self._base_row is None and self._new_row is None:
            self._status.setText(f"Found {n_sv} snapshot(s).{hint}  Click one to set base.")
        elif self._base_row is not None and self._new_row is None:
            base_name = self._subvolumes[self._base_row].path
            self._status.setText(f"Base: {base_name}  — now click another to set new.")
        elif self._base_row is None and self._new_row is not None:
            new_name = self._subvolumes[self._new_row].path
            self._status.setText(f"New: {new_name}  — now click another to set base.")
        else:
            assert self._base_row is not None and self._new_row is not None
            base_name = self._subvolumes[self._base_row].path
            new_name = self._subvolumes[self._new_row].path
            self._status.setText(f"Base: {base_name}  →  New: {new_name}  — click Compare.")

    def _on_compare(self) -> None:
        base_row = self._base_row
        new_row = self._new_row

        if not self._subvolumes:
            self._status.setText("Load snapshots first.")
            return
        if base_row is None or new_row is None:
            self._status.setText("Select both a base and a new snapshot.")
            return

        fs_root = os.path.normpath(self._fs_edit.text().strip() or "/")
        base_path = os.path.join(fs_root, self._subvolumes[base_row].path)
        new_path = os.path.join(fs_root, self._subvolumes[new_row].path)
        self._status.setText("Computing diff…")
        self.diff_requested.emit(base_path, new_path)
