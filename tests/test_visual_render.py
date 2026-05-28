# tests/test_visual_render.py
"""
Visual render test — builds a synthetic diff tree and saves PNG screenshots for
human inspection and code-quality feedback.

Run a single test:
    uv run pytest tests/test_visual_render.py -v -s

Output is written to:   tests/output/render.png

The image shows a QSplitter containing:
  • Left  (350 px) — DiffTreeView (collapsible file tree)
  • Right (850 px) — TreemapWidget (squarified colour-coded treemap)

Open tests/output/render.png in any image viewer to inspect the layout.
"""
from __future__ import annotations

import os
import sys

# Must be set *before* QApplication is created so Qt picks the offscreen backend.
# This makes the test work without a live X11/Wayland display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from btrmap.btrfs.diff import ChangeRecord, ChangeType
from btrmap.model.diff_tree import DiffTree

OUTPUT_DIR = Path(__file__).parent / "output"

# ── Synthetic dataset ─────────────────────────────────────────────────────────


def _recs(prefix: str, names: list[str], ct: ChangeType) -> list[ChangeRecord]:
    return [ChangeRecord(ct, f"{prefix}/{n}") for n in names]


def _build_synthetic_tree() -> DiffTree:
    """
    Simulate a realistic Arch Linux system snapshot diff:
      • kernel modules upgrade 6.12 → 6.13  (DELETED + CREATED, large)
      • Python 3.12 → 3.13 stdlib            (DELETED + CREATED, medium)
      • Firefox browser update                (MODIFIED, large libxul.so)
      • System libs & binaries               (MODIFIED + PERMISSIONS)
      • etc/ configs                          (PERMISSIONS + one MODIFIED)
      • var/log rotation                      (PERMISSIONS + MODIFIED)
      • New user document                     (CREATED)
      • Renamed notes file                    (RENAMED → DELETED + CREATED)
    """
    records: list[ChangeRecord] = []

    # Kernel modules: old version deleted
    records += _recs(
        "usr/lib/modules/6.12.0-arch1/kernel/drivers",
        [
            "net/ethernet/intel/igb.ko",
            "net/wireless/ath/ath9k.ko",
            "gpu/drm/amdgpu/amdgpu.ko",
            "usb/storage/usb-storage.ko",
            "fs/ext4/ext4.ko",
            "fs/btrfs/btrfs.ko",
        ],
        ChangeType.DELETED,
    )

    # Kernel modules: new version created
    records += _recs(
        "usr/lib/modules/6.13.0-arch1/kernel/drivers",
        [
            "net/ethernet/intel/igb.ko",
            "net/wireless/ath/ath9k.ko",
            "gpu/drm/amdgpu/amdgpu.ko",
            "usb/storage/usb-storage.ko",
            "fs/ext4/ext4.ko",
            "fs/btrfs/btrfs.ko",
            "gpu/drm/nouveau/nouveau.ko",  # new driver in 6.13
        ],
        ChangeType.CREATED,
    )

    # Python stdlib: old version deleted
    records += _recs(
        "usr/lib/python3.12",
        [
            "asyncio/__init__.py",
            "asyncio/base_events.py",
            "asyncio/tasks.py",
            "pathlib.py",
            "collections/__init__.py",
            "json/__init__.py",
            "json/decoder.py",
            "urllib/parse.py",
            "unittest/__init__.py",
        ],
        ChangeType.DELETED,
    )

    # Python stdlib: new version created
    records += _recs(
        "usr/lib/python3.13",
        [
            "asyncio/__init__.py",
            "asyncio/base_events.py",
            "asyncio/tasks.py",
            "pathlib.py",
            "collections/__init__.py",
            "json/__init__.py",
            "json/decoder.py",
            "urllib/parse.py",
            "unittest/__init__.py",
            "typing_extensions.py",  # new in 3.13
        ],
        ChangeType.CREATED,
    )

    # Firefox update: large shared library + JS bundle modified
    records += _recs(
        "usr/lib/firefox",
        [
            "firefox",
            "libxul.so",
            "libmozsqlite3.so",
            "browser/chrome/browser/content/browser/browser.js",
            "browser/omni.ja",
        ],
        ChangeType.MODIFIED,
    )

    # System library updates
    records += _recs(
        "usr/lib",
        ["libz.so.1.3.1", "libssl.so.3", "libcrypto.so.3", "libcurl.so.4"],
        ChangeType.MODIFIED,
    )

    # Binary permission/timestamp bumps (utimes after package install)
    records += _recs(
        "usr/bin",
        ["find", "grep", "sed", "awk", "python3", "firefox"],
        ChangeType.PERMISSIONS,
    )
    records += _recs(
        "etc",
        [
            "ld.so.cache",
            "resolv.conf",
            "ca-certificates/trust-source/README",
            "pacman.d/mirrorlist",
        ],
        ChangeType.PERMISSIONS,
    )
    records.append(ChangeRecord(ChangeType.MODIFIED, "etc/resolv.conf"))
    records += _recs(
        "var/log",
        ["pacman.log", "Xorg.0.log", "journal/machine-id"],
        ChangeType.PERMISSIONS,
    )
    records.append(ChangeRecord(ChangeType.MODIFIED, "var/log/pacman.log"))

    # Home directory changes
    records.append(
        ChangeRecord(ChangeType.CREATED, "home/user/Documents/project_report.pdf")
    )
    records.append(
        ChangeRecord(
            ChangeType.RENAMED,
            "home/user/notes_v2.txt",
            old_path="home/user/notes_v1.txt",
        )
    )

    tree = DiffTree.build(records)

    # Inject realistic file sizes (bytes) on each leaf node.
    # Unmapped leaves default to 50 KB so they still appear in the treemap.
    SIZES: dict[str, int] = {
        # Old kernel modules (DELETED — red)
        "usr/lib/modules/6.12.0-arch1/kernel/drivers/net/ethernet/intel/igb.ko": 4_200_000,
        "usr/lib/modules/6.12.0-arch1/kernel/drivers/net/wireless/ath/ath9k.ko": 3_100_000,
        "usr/lib/modules/6.12.0-arch1/kernel/drivers/gpu/drm/amdgpu/amdgpu.ko": 28_000_000,
        "usr/lib/modules/6.12.0-arch1/kernel/drivers/usb/storage/usb-storage.ko": 900_000,
        "usr/lib/modules/6.12.0-arch1/kernel/drivers/fs/ext4/ext4.ko": 2_100_000,
        "usr/lib/modules/6.12.0-arch1/kernel/drivers/fs/btrfs/btrfs.ko": 5_600_000,
        # New kernel modules (CREATED — green)
        "usr/lib/modules/6.13.0-arch1/kernel/drivers/net/ethernet/intel/igb.ko": 4_350_000,
        "usr/lib/modules/6.13.0-arch1/kernel/drivers/net/wireless/ath/ath9k.ko": 3_200_000,
        "usr/lib/modules/6.13.0-arch1/kernel/drivers/gpu/drm/amdgpu/amdgpu.ko": 29_500_000,
        "usr/lib/modules/6.13.0-arch1/kernel/drivers/usb/storage/usb-storage.ko": 920_000,
        "usr/lib/modules/6.13.0-arch1/kernel/drivers/fs/ext4/ext4.ko": 2_200_000,
        "usr/lib/modules/6.13.0-arch1/kernel/drivers/fs/btrfs/btrfs.ko": 5_800_000,
        "usr/lib/modules/6.13.0-arch1/kernel/drivers/gpu/drm/nouveau/nouveau.ko": 12_000_000,
        # Old Python stdlib (DELETED — red)
        "usr/lib/python3.12/asyncio/__init__.py": 8_000,
        "usr/lib/python3.12/asyncio/base_events.py": 80_000,
        "usr/lib/python3.12/asyncio/tasks.py": 40_000,
        "usr/lib/python3.12/pathlib.py": 56_000,
        "usr/lib/python3.12/collections/__init__.py": 64_000,
        "usr/lib/python3.12/json/__init__.py": 16_000,
        "usr/lib/python3.12/json/decoder.py": 28_000,
        "usr/lib/python3.12/urllib/parse.py": 72_000,
        "usr/lib/python3.12/unittest/__init__.py": 12_000,
        # New Python stdlib (CREATED — green)
        "usr/lib/python3.13/asyncio/__init__.py": 8_200,
        "usr/lib/python3.13/asyncio/base_events.py": 82_000,
        "usr/lib/python3.13/asyncio/tasks.py": 41_500,
        "usr/lib/python3.13/pathlib.py": 60_000,
        "usr/lib/python3.13/collections/__init__.py": 65_000,
        "usr/lib/python3.13/json/__init__.py": 16_500,
        "usr/lib/python3.13/json/decoder.py": 29_000,
        "usr/lib/python3.13/urllib/parse.py": 74_000,
        "usr/lib/python3.13/unittest/__init__.py": 12_500,
        "usr/lib/python3.13/typing_extensions.py": 180_000,
        # Firefox (MODIFIED — orange) — libxul dominates
        "usr/lib/firefox/firefox": 650_000,
        "usr/lib/firefox/libxul.so": 120_000_000,
        "usr/lib/firefox/libmozsqlite3.so": 3_600_000,
        "usr/lib/firefox/browser/chrome/browser/content/browser/browser.js": 900_000,
        "usr/lib/firefox/browser/omni.ja": 40_000_000,
        # System libs (MODIFIED — orange)
        "usr/lib/libz.so.1.3.1": 120_000,
        "usr/lib/libssl.so.3": 680_000,
        "usr/lib/libcrypto.so.3": 4_200_000,
        "usr/lib/libcurl.so.4": 520_000,
        # Binaries (PERMISSIONS — grey)
        "usr/bin/find": 240_000,
        "usr/bin/grep": 200_000,
        "usr/bin/sed": 160_000,
        "usr/bin/awk": 280_000,
        "usr/bin/python3": 8_000,
        "usr/bin/firefox": 4_000,
        # etc (PERMISSIONS + one MODIFIED)
        "etc/ld.so.cache": 800_000,
        "etc/resolv.conf": 200,
        "etc/ca-certificates/trust-source/README": 1_200,
        "etc/pacman.d/mirrorlist": 4_200,
        # var/log (PERMISSIONS + MODIFIED)
        "var/log/pacman.log": 520_000,
        "var/log/Xorg.0.log": 40_000,
        "var/log/journal/machine-id": 33,
        # Home (CREATED + DELETED from rename)
        "home/user/Documents/project_report.pdf": 2_400_000,
        "home/user/notes_v2.txt": 8_400,   # CREATED (rename target)
        "home/user/notes_v1.txt": 8_400,   # DELETED (rename source)
    }

    for leaf in tree.iter_leaves():
        leaf.size_bytes = SIZES.get(leaf.full_path, 50_000)

    return tree


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_render_treemap_and_treeview() -> None:
    """
    Render a 1200 × 700 composite widget (tree view + treemap) to PNG.

    Inspect the output at:  tests/output/render.png
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QSplitter

    from btrmap.ui.tree_view import DiffTreeView
    from btrmap.ui.treemap import TreemapWidget

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv[:1])

    tree = _build_synthetic_tree()

    # Build the same side-by-side layout as MainWindow
    splitter = QSplitter(Qt.Orientation.Horizontal)
    tree_view = DiffTreeView()
    treemap = TreemapWidget()
    splitter.addWidget(tree_view)
    splitter.addWidget(treemap)
    splitter.setSizes([350, 850])
    splitter.resize(1200, 700)

    tree_view.set_tree(tree)
    tree_view.expandToDepth(2)
    treemap.set_root(tree.root)

    # show() triggers layout; processEvents() ensures paint is flushed
    splitter.show()
    app.processEvents()

    pixmap = splitter.grab()

    # Basic geometry sanity checks before saving
    assert pixmap.width() == 1200, f"Unexpected width {pixmap.width()}"
    assert pixmap.height() == 700, f"Unexpected height {pixmap.height()}"

    out_path = OUTPUT_DIR / "render.png"
    saved = pixmap.save(str(out_path), "PNG")
    assert saved, f"QPixmap.save() returned False — check {out_path}"

    size_kb = out_path.stat().st_size // 1024
    print(f"\n  ✓ Saved {out_path.resolve()}  ({size_kb} KB)")
    assert size_kb > 20, f"PNG only {size_kb} KB — likely a blank frame"

    splitter.close()


def test_render_snapshot_selector() -> None:
    """Render the SnapshotSelector with synthetic snapshot data to PNG.

    Inspect the output at:  tests/output/selector.png
    """
    from unittest.mock import patch

    from PyQt6.QtWidgets import QApplication

    from btrmap.btrfs.subvolumes import Subvolume
    from btrmap.ui.snapshot_selector import SnapshotSelector

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv[:1])

    fake_subvolumes = [
        Subvolume(id=i, path=f".snapshots/{6080 + i}/snapshot",
                  mount_point=None, is_readonly=True, generation=700000 + i)
        for i in range(8)
    ]

    with patch("btrmap.ui.snapshot_selector.list_subvolumes", return_value=fake_subvolumes):
        sel = SnapshotSelector()
        sel.resize(800, 220)
        sel._load_snapshots()

    sel.show()
    app.processEvents()

    pixmap = sel.grab()
    out_path = OUTPUT_DIR / "selector.png"
    assert pixmap.save(str(out_path), "PNG")

    size_kb = out_path.stat().st_size // 1024
    print(f"\n  ✓ Saved {out_path.resolve()}  ({size_kb} KB)")
    assert size_kb > 2, f"PNG only {size_kb} KB — likely blank"

    sel.close()


def test_synthetic_tree_has_all_change_types() -> None:
    """Verify the synthetic dataset covers every ChangeType visible in the tree.

    Note: RENAMED input records are expanded by DiffTree.build() into a DELETED
    leaf (old path) and a CREATED leaf (new path), so RENAMED itself never
    appears on a leaf node — that is correct, expected behaviour.
    """
    from btrmap.btrfs.diff import ChangeType

    tree = _build_synthetic_tree()
    types_found = {node.change_type for node in tree.iter_leaves()}

    # These four types must appear as leaf change_types after tree construction.
    expected_leaf_types = {
        ChangeType.CREATED,
        ChangeType.MODIFIED,
        ChangeType.DELETED,
        ChangeType.PERMISSIONS,
    }
    for ct in expected_leaf_types:
        assert ct in types_found, f"No leaf with change_type={ct} in synthetic dataset"

    # RENAMED is consumed by build() and must NOT appear on any leaf.
    assert ChangeType.RENAMED not in types_found, (
        "RENAMED should be expanded into DELETED+CREATED, not appear on leaves"
    )


def test_synthetic_tree_sizes_nonzero() -> None:
    """Every leaf in the synthetic tree must have a positive size."""
    tree = _build_synthetic_tree()
    for leaf in tree.iter_leaves():
        assert leaf.size_bytes > 0, f"{leaf.full_path} has size 0"
