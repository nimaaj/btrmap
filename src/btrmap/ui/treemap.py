"""Squarified treemap widget for visualising snapshot diff sizes by file.

:func:`squarify` is a pure function (Bruls et al. 2000 algorithm) that returns
``(DiffNode, Rect)`` pairs — no Qt dependency, fully unit-testable.
:class:`TreemapWidget` caches the computed layout and recomputes it only on
:meth:`~TreemapWidget.set_root` or widget resize.
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QResizeEvent
from PyQt6.QtWidgets import QWidget

from btrmap.btrfs.diff import ChangeType
from btrmap.model.diff_tree import DiffNode


@dataclass
class Rect:
    """Axis-aligned rectangle used by the squarify layout algorithm."""

    x: float
    y: float
    w: float
    h: float


# ── Squarified treemap algorithm (Bruls et al. 2000) ──────────────────────────


def _worst_ratio(row: list[float], side: float) -> float:
    if not row or side == 0:
        return float("inf")
    s = sum(row)
    if s == 0:
        return float("inf")
    return max(side * side * max(row) / (s * s), s * s / (side * side * min(row)))


def _layout_row(row: list[float], rect: Rect, horizontal: bool) -> tuple[list[Rect], Rect]:
    """Place row along the short edge of rect; return (placed rects, remaining rect)."""
    s = sum(row)
    if horizontal:
        h = s / rect.w if rect.w else 0
        cx = rect.x
        placed = []
        for r in row:
            w = r / h if h else 0
            placed.append(Rect(cx, rect.y, w, h))
            cx += w
        remaining = Rect(rect.x, rect.y + h, rect.w, rect.h - h)
    else:
        w = s / rect.h if rect.h else 0
        cy = rect.y
        placed = []
        for r in row:
            h = r / w if w else 0
            placed.append(Rect(rect.x, cy, w, h))
            cy += h
        remaining = Rect(rect.x + w, rect.y, rect.w - w, rect.h)
    return placed, remaining


def _squarify_rects(areas: list[float], rect: Rect) -> list[Rect]:
    """
    Given normalised areas (summing to rect.w * rect.h), return a Rect for each.
    Implements the squarified treemap algorithm.
    """
    if not areas:
        return []
    if len(areas) == 1:
        return [Rect(rect.x, rect.y, rect.w, rect.h)]

    horizontal = rect.w >= rect.h
    side = rect.w if horizontal else rect.h

    row: list[float] = []
    remaining = rect
    result: list[Rect] = []
    idx = 0

    while idx < len(areas):
        candidate = areas[idx]
        candidate_row = row + [candidate]
        if not row or _worst_ratio(candidate_row, side) <= _worst_ratio(row, side):
            row.append(candidate)
            idx += 1
        else:
            placed, remaining = _layout_row(row, remaining, horizontal)
            result.extend(placed)
            horizontal = remaining.w >= remaining.h
            side = remaining.w if horizontal else remaining.h
            row = []

    if row:
        placed, _ = _layout_row(row, remaining, horizontal)
        result.extend(placed)

    return result


def squarify(
    node: DiffNode,
    rect: Rect,
    min_area: float = 4.0,
) -> list[tuple[DiffNode, Rect]]:
    """
    Return (node, rect) pairs for all nodes whose rect area >= min_area.
    Recursively lays out children using the squarified treemap algorithm.
    Nodes with total_size == 0 receive equal area among siblings.
    Pure function — does not mutate the tree.
    """
    children = list(node.children.values())
    if not children:
        # Leaf node
        return [(node, rect)] if rect.w * rect.h >= min_area else []

    # Compute sizes, giving zero-size nodes equal share
    raw = [c.total_size for c in children]
    total = sum(raw)
    if total == 0:
        sizes = [1.0] * len(children)
        total = float(len(children))
    else:
        n_zero = sum(1 for s in raw if s == 0)
        if n_zero:
            avg = total / (len(raw) - n_zero)
            sizes = [float(s) if s > 0 else avg for s in raw]
            total = sum(sizes)
        else:
            sizes = [float(s) for s in raw]

    # Normalise so areas sum to rect.w * rect.h
    rect_area = rect.w * rect.h
    areas = [s * rect_area / total for s in sizes]

    child_rects = _squarify_rects(areas, rect)

    result: list[tuple[DiffNode, Rect]] = []
    for child, child_rect in zip(children, child_rects, strict=True):
        if child_rect.w * child_rect.h < min_area:
            continue
        if child.is_leaf:
            result.append((child, child_rect))
        else:
            result.extend(squarify(child, child_rect, min_area))
    return result


CHANGE_TYPE_COLORS: dict[ChangeType | None, QColor] = {
    ChangeType.CREATED: QColor("#4caf50"),
    ChangeType.MODIFIED: QColor("#ff9800"),
    ChangeType.DELETED: QColor("#f44336"),
    ChangeType.RENAMED: QColor("#2196f3"),
    ChangeType.PERMISSIONS: QColor("#9e9e9e"),
    None: QColor("#424242"),
}

# Human-readable labels used in the on-canvas legend
_CHANGE_LABELS: dict[ChangeType | None, str] = {
    ChangeType.CREATED: "Created",
    ChangeType.MODIFIED: "Modified",
    ChangeType.DELETED: "Deleted",
    ChangeType.RENAMED: "Renamed",
    ChangeType.PERMISSIONS: "Permissions",
}

# Semi-transparent black used for cell borders — colour-agnostic, so adjacent
# cells of the same type still visually separate.
_BORDER_COLOR = QColor(0, 0, 0, 100)
_SELECTION_OVERLAY = QColor(255, 255, 255, 70)


def _fmt_size(n: int) -> str:
    """Format byte count as human-readable string."""
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n}{unit}"
        n //= 1024
    return f"{n}T"


class TreemapWidget(QWidget):
    """Canvas widget that renders a squarified treemap of a :class:`~btrmap.model.diff_tree.DiffNode` subtree.

    Click a cell to select that node (emits :attr:`node_selected`).  The layout is
    cached until the root changes or the widget is resized.
    """

    node_selected = pyqtSignal(str)  # emits full_path

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root: DiffNode | None = None
        self._layout_cache: list[tuple[DiffNode, Rect]] = []
        self._selected_path: str | None = None
        self.setMinimumSize(200, 200)

    def set_root(self, node: DiffNode) -> None:
        self._root = node
        self._layout_cache = []
        self.update()

    def select_node(self, full_path: str) -> None:
        self._selected_path = full_path
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1e1e1e"))
        if self._root is None:
            return

        if not self._layout_cache:
            bounds = Rect(0.0, 0.0, float(self.width()), float(self.height()))
            self._layout_cache = squarify(self._root, bounds)

        # ── Draw cells ────────────────────────────────────────────────────────
        for node, r in self._layout_cache:
            color = CHANGE_TYPE_COLORS.get(node.change_type, CHANGE_TYPE_COLORS[None])
            qr = QRectF(r.x + 0.5, r.y + 0.5, r.w - 1.0, r.h - 1.0)

            painter.fillRect(qr, color)

            # Universal semi-transparent dark border — separates cells of the
            # same colour cleanly without a hue shift.
            painter.setPen(_BORDER_COLOR)
            painter.drawRect(qr)

            # Selection highlight
            if node.full_path == self._selected_path:
                painter.fillRect(qr, _SELECTION_OVERLAY)

            # Label: filename on line 1; size on line 2 when space allows
            if r.w > 44 and r.h > 18:
                inner = qr.adjusted(3, 3, -3, -3)
                painter.setPen(QColor("white"))

                if r.h > 36 and r.w > 60:
                    # Two-line label: name + size
                    name_rect = QRectF(inner.x(), inner.y(), inner.width(), inner.height() / 2)
                    size_rect = QRectF(
                        inner.x(), inner.y() + inner.height() / 2,
                        inner.width(), inner.height() / 2,
                    )
                    painter.drawText(
                        name_rect,
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                        node.name,
                    )
                    small = QFont(painter.font())
                    small.setPointSizeF(max(small.pointSizeF() - 1.5, 6.0))
                    painter.setFont(small)
                    painter.setPen(QColor(220, 220, 220))
                    painter.drawText(
                        size_rect,
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                        _fmt_size(node.size_bytes),
                    )
                    painter.setFont(QFont())  # reset
                else:
                    # Single-line: filename only
                    painter.drawText(
                        inner,
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        node.name,
                    )

        # ── Colour legend (bottom-right corner) ───────────────────────────────
        self._draw_legend(painter)

    def _draw_legend(self, painter: QPainter) -> None:
        """Draw a compact colour legend anchored to the bottom-right corner."""
        items = [
            (ChangeType.CREATED, _CHANGE_LABELS[ChangeType.CREATED]),
            (ChangeType.MODIFIED, _CHANGE_LABELS[ChangeType.MODIFIED]),
            (ChangeType.DELETED, _CHANGE_LABELS[ChangeType.DELETED]),
            (ChangeType.PERMISSIONS, _CHANGE_LABELS[ChangeType.PERMISSIONS]),
            (ChangeType.RENAMED, _CHANGE_LABELS[ChangeType.RENAMED]),
        ]

        swatch = 10  # colour square side length (px)
        row_h = 14   # height per legend row
        pad = 6
        text_w = 80
        legend_w = pad + swatch + 4 + text_w + pad
        legend_h = pad + len(items) * row_h + pad

        lx = float(self.width() - legend_w - 4)
        ly = float(self.height() - legend_h - 4)

        # Semi-transparent background
        painter.fillRect(QRectF(lx, ly, legend_w, legend_h), QColor(0, 0, 0, 160))
        painter.setPen(QColor(80, 80, 80))
        painter.drawRect(QRectF(lx, ly, legend_w, legend_h))

        small_font = QFont(painter.font())
        small_font.setPointSizeF(max(small_font.pointSizeF() - 1.5, 6.5))
        painter.setFont(small_font)

        for i, (ct, label) in enumerate(items):
            ry = ly + pad + i * row_h
            color = CHANGE_TYPE_COLORS[ct]
            # Swatch
            painter.fillRect(QRectF(lx + pad, ry + 1, swatch, swatch), color)
            painter.setPen(_BORDER_COLOR)
            painter.drawRect(QRectF(lx + pad, ry + 1, swatch, swatch))
            # Label
            painter.setPen(QColor("white"))
            painter.drawText(
                QRectF(lx + pad + swatch + 4, ry, text_w, row_h),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

        painter.setFont(QFont())  # reset

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        x = float(event.position().x())
        y = float(event.position().y())
        for node, r in self._layout_cache:
            if r.x <= x < r.x + r.w and r.y <= y < r.y + r.h:
                self._selected_path = node.full_path
                self.node_selected.emit(node.full_path)
                self.update()
                return

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._layout_cache = []
        super().resizeEvent(event)
