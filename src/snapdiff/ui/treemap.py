# src/snapdiff/ui/treemap.py
from __future__ import annotations

from dataclasses import dataclass

from snapdiff.model.diff_tree import DiffNode

# PyQt6 imports are added in Task 9 (widget section). Only pure types here.


@dataclass
class Rect:
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
    for child, child_rect in zip(children, child_rects):
        if child_rect.w * child_rect.h < min_area:
            continue
        if child.is_leaf:
            result.append((child, child_rect))
        else:
            result.extend(squarify(child, child_rect, min_area))
    return result


# ── Append to src/snapdiff/ui/treemap.py ──────────────────────────────────────

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QResizeEvent
from PyQt6.QtWidgets import QWidget

from snapdiff.btrfs.diff import ChangeType

CHANGE_TYPE_COLORS: dict[ChangeType | None, QColor] = {
    ChangeType.CREATED:     QColor("#4caf50"),
    ChangeType.MODIFIED:    QColor("#ff9800"),
    ChangeType.DELETED:     QColor("#f44336"),
    ChangeType.RENAMED:     QColor("#2196f3"),
    ChangeType.PERMISSIONS: QColor("#9e9e9e"),
    None:                   QColor("#424242"),
}


class TreemapWidget(QWidget):
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
        painter.fillRect(self.rect(), QColor("#1e1e1e"))
        if self._root is None:
            return

        if not self._layout_cache:
            bounds = Rect(0.0, 0.0, float(self.width()), float(self.height()))
            self._layout_cache = squarify(self._root, bounds)

        from PyQt6.QtCore import QRectF

        for node, r in self._layout_cache:
            color = CHANGE_TYPE_COLORS.get(node.change_type, CHANGE_TYPE_COLORS[None])
            qr = QRectF(r.x, r.y, r.w, r.h)
            painter.fillRect(qr, color)
            painter.setPen(color.darker(130))
            painter.drawRect(qr)

            if node.full_path == self._selected_path:
                painter.fillRect(qr, QColor(255, 255, 255, 60))

            if r.w > 40 and r.h > 20:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(QColor("white"))
                painter.drawText(
                    qr.adjusted(3, 3, -3, -3),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    node.name,
                )
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

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
