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
