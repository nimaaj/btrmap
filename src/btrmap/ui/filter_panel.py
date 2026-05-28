"""Horizontal filter bar: change-type checkboxes, dotfile toggle, min-size spinner, path search.

All controls start disabled and are enabled via :meth:`FilterPanel.set_enabled` once a diff
loads.  Every control change emits :attr:`FilterPanel.filter_changed` with the current
:class:`~btrmap.model.filter.FilterSpec`.  The ``Ctrl+F`` and ``Ctrl+R`` keyboard shortcuts
are wired by the parent :class:`~btrmap.ui.main_window.MainWindow`.
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from btrmap.btrfs.diff import ChangeType
from btrmap.model.filter import FilterSpec

_TYPE_LABELS: dict[ChangeType, str] = {
    ChangeType.CREATED: "Created",
    ChangeType.MODIFIED: "Modified",
    ChangeType.DELETED: "Deleted",
    ChangeType.RENAMED: "Renamed",
    ChangeType.PERMISSIONS: "Permissions",
}


class FilterPanel(QWidget):
    """Compact horizontal bar of filter controls.

    Emits ``filter_changed(FilterSpec)`` whenever any control changes.
    All controls start disabled; call ``set_enabled(True)`` once a diff loads.
    """

    filter_changed = pyqtSignal(object)  # emits FilterSpec

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._building = False  # suppress intermediate signals during reset()
        self._type_checks: dict[ChangeType, QCheckBox] = {}
        self._setup_ui()
        self.set_enabled(False)

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        # ── Change-type checkboxes ────────────────────────────────────────────
        layout.addWidget(QLabel("Show:"))
        for ct in ChangeType:
            cb = QCheckBox(_TYPE_LABELS[ct])
            cb.setChecked(True)
            cb.toggled.connect(self._on_change)
            self._type_checks[ct] = cb
            layout.addWidget(cb)

        layout.addSpacing(8)

        # ── Dotfile toggle ────────────────────────────────────────────────────
        self._dotfile_cb = QCheckBox("Hide dotfiles")
        self._dotfile_cb.setChecked(False)
        self._dotfile_cb.toggled.connect(self._on_change)
        layout.addWidget(self._dotfile_cb)

        layout.addSpacing(8)

        # ── Minimum size ──────────────────────────────────────────────────────
        layout.addWidget(QLabel("Min size:"))
        self._min_size_spin = QSpinBox()
        self._min_size_spin.setRange(0, 1_000_000)
        self._min_size_spin.setValue(0)
        self._min_size_spin.setSuffix(" KB")
        self._min_size_spin.setSpecialValueText("off")  # shows "off" instead of "0 KB"
        self._min_size_spin.valueChanged.connect(self._on_change)
        layout.addWidget(self._min_size_spin)

        layout.addSpacing(8)

        # ── Path search ───────────────────────────────────────────────────────
        layout.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("filter by path…")
        self._search_edit.setMinimumWidth(180)
        self._search_edit.setMaximumWidth(260)
        self._search_edit.textChanged.connect(self._on_change)
        layout.addWidget(self._search_edit)

        layout.addSpacing(8)

        # ── Reset ─────────────────────────────────────────────────────────────
        self._reset_btn = QPushButton("Reset")
        self._reset_btn.setFixedWidth(60)
        self._reset_btn.setToolTip("Restore all filter defaults (Ctrl+R)")
        self._reset_btn.clicked.connect(self.reset)
        layout.addWidget(self._reset_btn)

        layout.addStretch()

    # ── Public API ────────────────────────────────────────────────────────────

    def current_spec(self) -> FilterSpec:
        """Return the current filter state as an immutable FilterSpec."""
        enabled = frozenset(ct for ct, cb in self._type_checks.items() if cb.isChecked())
        return FilterSpec(
            enabled_types=enabled,
            hide_dotfiles=self._dotfile_cb.isChecked(),
            min_size_bytes=self._min_size_spin.value() * 1024,
            path_search=self._search_edit.text().strip(),
        )

    def reset(self) -> None:
        """Restore all defaults and emit a single filter_changed signal."""
        self._building = True
        for cb in self._type_checks.values():
            cb.setChecked(True)
        self._dotfile_cb.setChecked(False)
        self._min_size_spin.setValue(0)
        self._search_edit.clear()
        self._building = False
        self._on_change()

    def focus_search(self) -> None:
        """Focus the path search field (Ctrl+F target)."""
        self._search_edit.setFocus()
        self._search_edit.selectAll()

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable all controls (disabled while no diff is loaded)."""
        for cb in self._type_checks.values():
            cb.setEnabled(enabled)
        self._dotfile_cb.setEnabled(enabled)
        self._min_size_spin.setEnabled(enabled)
        self._search_edit.setEnabled(enabled)
        self._reset_btn.setEnabled(enabled)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _on_change(self) -> None:
        if not self._building:
            self.filter_changed.emit(self.current_spec())
