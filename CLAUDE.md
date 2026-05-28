# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Spec File

`btrfs_snapdiff_spec.md` must be present at the repo root. It is the authoritative
reference for all data structures, CLI commands, error types, and color constants.
When this file and the spec conflict, the spec wins.

## Project Overview

`btrfs-snapdiff` is a PyQt6 desktop GUI tool that visually compares two read-only btrfs
snapshots. It uses `btrfs send --no-data | btrfs receive --dump` to produce a diff,
aggregates it into a tree, and renders both a collapsible `QTreeView` and a squarified
treemap side-by-side.

## Commands

```bash
# Install all dependencies including dev
uv sync --dev

# Run the app (btrfs send requires root)
sudo uv run snapdiff

# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/test_diff_parser.py::test_name -v

# Lint
uv run ruff check src/

# Format
uv run ruff format src/
```

## Project Setup

`pyproject.toml` must declare:

```toml
[project]
name = "btrfs-snapdiff"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "PyQt6>=6.6",
]

[project.scripts]
snapdiff = "snapdiff.main:main"

[dependency-groups]
dev = [
    "pytest>=8",
    "ruff",
]

[tool.ruff]
target-version = "py311"
line-length = 100
```

## Architecture

```
src/snapdiff/
├── btrfs/          # CLI wrappers — ZERO PyQt6 imports
│   ├── subvolumes.py   # btrfs subvolume list -rpo → list[Subvolume]
│   └── diff.py         # btrfs send | btrfs receive --dump → list[ChangeRecord]
├── model/          # Data structures — ZERO PyQt6 imports
│   ├── diff_tree.py    # DiffNode / DiffTree; builds tree from ChangeRecord list
│   └── enrichment.py   # stat() files to populate size_bytes on each DiffNode
├── ui/             # PyQt6 widgets only
│   ├── main_window.py       # top-level layout, QSplitter, signal wiring
│   ├── snapshot_selector.py # filesystem root + two snapshot QComboBox + Compare button
│   ├── tree_view.py         # DiffTreeModel (QAbstractItemModel) + DiffTreeView
│   └── treemap.py           # squarify() pure function + TreemapWidget (QWidget)
├── utils/
│   └── subprocess.py   # ONLY place that calls subprocess; everything else imports this
└── main.py             # QApplication entry point
```

## Absolute Constraints

These are hard rules. Do not violate them under any circumstances:

- `btrfs/` and `model/` must have **zero PyQt6 imports**.
- All subprocess calls go exclusively through `utils/subprocess.py`. No other module
  may import `subprocess` directly.
- `squarify()` in `ui/treemap.py` must be a **pure function** with no side effects.
- No global mutable state. All state lives in widget instances or is passed explicitly.
- Do not use `os.system` anywhere.

## Threading Model

Long-running operations (`compute_diff`, `enrich`) must run in a `QThread` subclass:

```python
class WorkerThread(QThread):
    finished = pyqtSignal(object)
    error = pyqtSignal(Exception)
```

Flow: `SnapshotSelector.diff_requested` → `WorkerThread` → on `finished` signal, update
both `DiffTreeView` and `TreemapWidget`. Never block the main thread.

## Cross-Widget Synchronization

Both `DiffTreeView` and `TreemapWidget` expose:

```python
node_selected = pyqtSignal(str)  # emits full_path
```

Selecting a node in either widget must select the corresponding node in the other.
Use a re-entrancy flag (`_syncing: bool`) to prevent signal loops.

## Error Handling

Typed exceptions raised by the `btrfs/` layer:

| Exception | Trigger |
|---|---|
| `DiffError` | `compute_diff` subprocess non-zero exit; include stderr in message |
| `SubvolumeListError` | `list_subvolumes` subprocess failure |
| `PermissionError` | btrfs send exits EPERM; message must be human-readable |
| `RuntimeError` | `btrfs` binary not found on PATH; include install hint in message |

All exceptions are caught at the `QThread` boundary. Display via status bar for
recoverable errors, `QMessageBox` for fatal ones. The app must never crash on a
subprocess error. Running without root must produce a clear UI message, not a traceback.

## Key Implementation Details

### Parser mapping (`btrfs receive --dump` → `ChangeType`)

| Token(s) | ChangeType |
|---|---|
| `write`, `truncate` | MODIFIED |
| `mkfile`, `mkdir`, `mksock`, `mkfifo`, `symlink`, `link` | CREATED |
| `unlink`, `rmdir` | DELETED |
| `rename` | RENAMED |
| `chmod`, `chown`, `utimes`, `set_xattr` | PERMISSIONS |

Deduplication rule: if the same path accumulates both PERMISSIONS and MODIFIED records,
emit only MODIFIED.

### DiffTree

- `DiffTree.build()` splits each record's path on `/` to insert into the tree.
  Intermediate directory nodes get `change_type = None`.
- RENAMED records expand into two leaf nodes: `old_path` as DELETED, `path` as CREATED.
- `DiffNode.total_size` is the recursive sum of `size_bytes` across all leaf descendants.

### Enrichment

- CREATED / MODIFIED / PERMISSIONS: stat from new snapshot mount.
- DELETED: stat from base snapshot mount.
- RENAMED: stat new path from new snapshot mount.
- Stat failure (socket, pipe, path outside mount): silently set `size_bytes = 0`.

### Treemap

- Algorithm: squarified treemap (Bruls et al. 2000). Implement as a pure function.
- Nodes with `total_size == 0` receive equal area among siblings; do not skip them.
- Draw labels only when rect width > 40px and height > 20px.
- Cache the computed layout; invalidate on `set_root()` or widget resize.

### Persistence

- Splitter position persisted via `QSettings("btrfs-snapdiff", "main")`.

## Testing

### Constraints

- Tests must **never** invoke any `btrfs` CLI command.
- Tests must **never** require root.
- Mock all subprocess calls at the `utils/subprocess.py` boundary.

### Required test files

| File | What to cover |
|---|---|
| `tests/test_diff_parser.py` | `btrfs receive --dump` line parser: all operation tokens, paths with spaces, paths with Unicode, rename lines |
| `tests/test_diff_tree.py` | `DiffTree.build()`: correct parent-child structure, `total_size` aggregation, rename expansion to two leaf nodes |
| `tests/test_treemap_layout.py` | `squarify()`: all rects fit within bounding rect, no overlaps, area proportional to size within 1% tolerance, zero-size node handling |
