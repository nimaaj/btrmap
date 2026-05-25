# src/snapdiff/ui/snapshot_selector.py
from __future__ import annotations

import os

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from snapdiff.btrfs.subvolumes import Subvolume, SubvolumeListError, list_subvolumes


class SnapshotSelector(QWidget):
    diff_requested = pyqtSignal(str, str)  # (base_absolute_path, new_absolute_path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._subvolumes: list[Subvolume] = []

        # Filesystem root row
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

        # Snapshot selector row
        self._base_combo = QComboBox()
        self._new_combo = QComboBox()
        compare_btn = QPushButton("Compare")
        compare_btn.clicked.connect(self._on_compare)

        snap_row = QHBoxLayout()
        snap_row.addWidget(QLabel("Base:"))
        snap_row.addWidget(self._base_combo, stretch=1)
        snap_row.addWidget(QLabel("New:"))
        snap_row.addWidget(self._new_combo, stretch=1)
        snap_row.addWidget(compare_btn)

        self._status = QLabel("")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(fs_row)
        layout.addLayout(snap_row)
        layout.addWidget(self._status)

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

        self._base_combo.clear()
        self._new_combo.clear()
        for sv in self._subvolumes:
            self._base_combo.addItem(sv.path)
            self._new_combo.addItem(sv.path)
        if len(self._subvolumes) >= 2:
            self._new_combo.setCurrentIndex(1)
        self._status.setText(f"Found {len(self._subvolumes)} read-only subvolume(s).")

    def _on_compare(self) -> None:
        base_idx = self._base_combo.currentIndex()
        new_idx = self._new_combo.currentIndex()

        if not self._subvolumes:
            self._status.setText("Load snapshots first.")
            return
        if base_idx < 0 or new_idx < 0:
            self._status.setText("Select both snapshots.")
            return
        if base_idx == new_idx:
            self._status.setText("Base and new snapshots must be different.")
            return

        fs_root = self._fs_edit.text().strip().rstrip("/")
        base_path = os.path.join(fs_root, self._subvolumes[base_idx].path)
        new_path = os.path.join(fs_root, self._subvolumes[new_idx].path)
        self._status.setText("Computing diff…")
        self.diff_requested.emit(base_path, new_path)
