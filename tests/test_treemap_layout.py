# tests/test_treemap_layout.py
from __future__ import annotations

import pytest

from snapdiff.btrfs.diff import ChangeType
from snapdiff.model.diff_tree import DiffNode
from snapdiff.ui.treemap import Rect, squarify


def _leaf(name: str, size: int) -> DiffNode:
    return DiffNode(name=name, full_path=name, change_type=ChangeType.MODIFIED, size_bytes=size)


def _parent(*children: DiffNode) -> DiffNode:
    return DiffNode(
        name="root",
        full_path="",
        change_type=None,
        size_bytes=0,
        children={c.name: c for c in children},
    )


BOUNDS = Rect(0, 0, 400, 300)


def test_single_child_fills_rect() -> None:
    root = _parent(_leaf("a", 100))
    result = squarify(root, BOUNDS, min_area=0.0)
    assert len(result) == 1
    _, r = result[0]
    assert abs(r.x - 0) < 0.01
    assert abs(r.y - 0) < 0.01
    assert abs(r.w - BOUNDS.w) < 0.01
    assert abs(r.h - BOUNDS.h) < 0.01


def test_rects_fit_within_bounds() -> None:
    root = _parent(*[_leaf(f"f{i}", (i + 1) * 100) for i in range(10)])
    result = squarify(root, BOUNDS, min_area=0.0)
    for _, r in result:
        assert r.x >= -0.01, f"x={r.x} out of bounds"
        assert r.y >= -0.01, f"y={r.y} out of bounds"
        assert r.x + r.w <= BOUNDS.w + 0.01, f"right edge {r.x + r.w} exceeds {BOUNDS.w}"
        assert r.y + r.h <= BOUNDS.h + 0.01, f"bottom edge {r.y + r.h} exceeds {BOUNDS.h}"


def test_no_overlaps() -> None:
    root = _parent(*[_leaf(f"f{i}", (i + 1) * 50) for i in range(8)])
    result = squarify(root, BOUNDS, min_area=0.0)
    for i, (_, r1) in enumerate(result):
        for j, (_, r2) in enumerate(result):
            if i >= j:
                continue
            overlaps = not (
                r1.x + r1.w <= r2.x + 0.01
                or r2.x + r2.w <= r1.x + 0.01
                or r1.y + r1.h <= r2.y + 0.01
                or r2.y + r2.h <= r1.y + 0.01
            )
            assert not overlaps, f"Rects {i} ({r1}) and {j} ({r2}) overlap"


def test_area_proportional_to_size() -> None:
    sizes = [100, 200, 300, 400]
    root = _parent(*[_leaf(f"f{i}", s) for i, s in enumerate(sizes)])
    result = squarify(root, BOUNDS, min_area=0.0)
    total_size = sum(sizes)
    total_area = BOUNDS.w * BOUNDS.h
    assert len(result) == len(sizes)
    for (node, r), size in zip(result, sizes):
        expected = size * total_area / total_size
        actual = r.w * r.h
        rel_err = abs(actual - expected) / expected
        assert rel_err < 0.01, f"{node.name}: expected area {expected:.1f}, got {actual:.1f}"


def test_zero_size_nodes_receive_area() -> None:
    root = _parent(_leaf("big", 1000), _leaf("zero1", 0), _leaf("zero2", 0))
    result = squarify(root, BOUNDS, min_area=0.0)
    names = {node.name for node, _ in result}
    assert "zero1" in names, "zero-size node must appear in layout"
    assert "zero2" in names, "zero-size node must appear in layout"
    # Each zero-size node gets equal share of the area originally allocated to zero nodes
    rects = {node.name: r for node, r in result}
    assert rects["zero1"].w * rects["zero1"].h > 0
    assert rects["zero2"].w * rects["zero2"].h > 0


def test_min_area_filters_small_rects() -> None:
    # One large child, many tiny children
    root = _parent(_leaf("big", 10000), *[_leaf(f"tiny{i}", 1) for i in range(50)])
    result = squarify(root, BOUNDS, min_area=100.0)
    names = {node.name for node, _ in result}
    assert "big" in names
    # Tiny rects below min_area should be excluded
    tiny_count = sum(1 for name in names if name.startswith("tiny"))
    assert tiny_count < 50
