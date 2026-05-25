# AGENT SPEC: btrfs-snapdiff

A desktop GUI tool for visually inspecting the difference between two btrfs snapshots.
Built with PyQt6. Managed with `uv`.

---

## Goals

- Parse the diff between two read-only btrfs snapshots
- Display the diff as a synchronized treemap + tree view
- Encode change type (created/modified/deleted/renamed) by color
- Encode change magnitude (bytes) by rectangle area in the treemap
- Allow the user to select snapshot pairs interactively

---

## Non-Goals

- Do not implement snapshot creation, deletion, or rollback
- Do not require Snapper or any third-party snapshot manager
- Do not bundle btrfs-progs; assume it is installed on the host

---

## Project Structure

```
btrfs-snapdiff/
├── pyproject.toml
├── README.md
├── src/
│   └── snapdiff/
│       ├── __init__.py
│       ├── main.py              # entry point, constructs QApplication
│       ├── btrfs/
│       │   ├── __init__.py
│       │   ├── subvolumes.py    # enumerate subvolumes via btrfs CLI
│       │   └── diff.py          # run btrfs send --no-data, parse output
│       ├── model/
│       │   ├── __init__.py
│       │   ├── diff_tree.py     # DiffNode, DiffTree datastructures
│       │   └── enrichment.py    # stat files to get sizes
│       ├── ui/
│       │   ├── __init__.py
│       │   ├── main_window.py   # QMainWindow, layout
│       │   ├── snapshot_selector.py  # top bar widget
│       │   ├── tree_view.py     # QTreeView + QAbstractItemModel
│       │   └── treemap.py       # custom QWidget, squarified treemap
│       └── utils/
│           ├── __init__.py
│           └── subprocess.py    # thin wrapper around subprocess.run
└── tests/
    ├── test_diff_parser.py
    ├── test_diff_tree.py
    └── test_treemap_layout.py
```

---

## Dependencies

Declare in `pyproject.toml`:

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
```

No other runtime dependencies. Do not use `pip`; use `uv` for all environment operations.

---

## Module Specifications

### `btrfs/subvolumes.py`

Responsibility: enumerate available subvolumes on a given btrfs filesystem.

```python
@dataclass
class Subvolume:
    id: int
    path: str          # path relative to filesystem root
    mount_point: str | None
    is_readonly: bool
    generation: int

def list_subvolumes(fs_path: str) -> list[Subvolume]:
    """
    Run `btrfs subvolume list -rpo <fs_path>` and parse stdout.
    Raise SubvolumeListError on non-zero exit or parse failure.
    Only return read-only subvolumes (required for btrfs send).
    """
```

### `btrfs/diff.py`

Responsibility: produce a raw list of change records from `btrfs send --no-data`.

```python
class ChangeType(Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    PERMISSIONS = "permissions"   # utimes/chmod only, no data change

@dataclass
class ChangeRecord:
    change_type: ChangeType
    path: str
    old_path: str | None   # populated for RENAMED only

def compute_diff(
    base_snapshot: str,
    new_snapshot: str,
) -> list[ChangeRecord]:
    """
    Run:
        btrfs send --no-data -p <base_snapshot> <new_snapshot> |
        btrfs receive --dump

    Parse each output line into a ChangeRecord.
    Raise DiffError if either subprocess exits non-zero.
    Must be run as root or with CAP_SYS_ADMIN; raise PermissionError
    with a clear message if btrfs send exits with EPERM.

    Deduplication rule: if a path appears with both PERMISSIONS and
    MODIFIED changes, emit only MODIFIED.
    """
```

**Parser rules** for `btrfs receive --dump` output:

| Operation token | Maps to ChangeType |
|---|---|
| `write`, `truncate` | MODIFIED |
| `mkfile`, `mkdir`, `mksock`, `mkfifo` | CREATED |
| `symlink`, `link` | CREATED |
| `unlink`, `rmdir` | DELETED |
| `rename` | RENAMED |
| `chmod`, `chown`, `utimes`, `set_xattr` | PERMISSIONS |

### `model/diff_tree.py`

Responsibility: aggregate flat `ChangeRecord` list into a tree structure suitable for both the QTreeView model and treemap layout algorithm.

```python
@dataclass
class DiffNode:
    name: str                          # basename of path component
    full_path: str
    change_type: ChangeType | None     # None for intermediate directories
    size_bytes: int                    # populated by enrichment; 0 if unknown
    children: dict[str, "DiffNode"]   # keyed by name

    @property
    def total_size(self) -> int:
        """Recursive sum of size_bytes across all leaf descendants."""

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

class DiffTree:
    root: DiffNode

    @classmethod
    def build(cls, records: list[ChangeRecord]) -> "DiffTree":
        """
        Insert each record into the tree by splitting its path on '/'.
        Intermediate nodes get change_type=None.
        For RENAMED records, insert both old_path (DELETED) and
        path (CREATED) as separate leaf nodes.
        """

    def iter_leaves(self) -> Iterator[DiffNode]: ...
    def find(self, full_path: str) -> DiffNode | None: ...
```

### `model/enrichment.py`

Responsibility: stat files in the new (and base) snapshot mount to populate `size_bytes` on each leaf `DiffNode`.

```python
def enrich(
    tree: DiffTree,
    new_snapshot_mount: str,
    base_snapshot_mount: str,
) -> None:
    """
    For each leaf in tree:
    - CREATED / MODIFIED: stat from new_snapshot_mount
    - DELETED: stat from base_snapshot_mount
    - RENAMED: stat new path from new_snapshot_mount
    - PERMISSIONS: stat from new_snapshot_mount
    Silently set size_bytes=0 if stat fails (file may be a socket,
    pipe, or the mount may not cover the path).
    This function mutates the tree in place.
    """
```

### `ui/treemap.py`

Responsibility: render a squarified treemap from a `DiffNode` subtree.

**Algorithm:** squarified treemap (Bruls et al. 2000). Implement as a pure function:

```python
@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

def squarify(
    node: DiffNode,
    rect: Rect,
    min_area: float = 4.0,
) -> list[tuple[DiffNode, Rect]]:
    """
    Return a flat list of (node, rect) pairs for all nodes whose
    rect area >= min_area. Does not mutate the tree.
    Nodes with total_size == 0 are assigned equal area among siblings.
    """
```

**Widget:**

```python
class TreemapWidget(QWidget):
    node_selected = pyqtSignal(str)   # emits full_path

    def set_root(self, node: DiffNode) -> None: ...

    def paintEvent(self, event: QPaintEvent) -> None:
        """
        Recompute squarify layout on every paint (cache invalidated by
        set_root or resize). Draw each leaf rect with:
        - fill color from CHANGE_TYPE_COLORS
        - 1px border in a slightly darker shade
        - filename label if rect is wide and tall enough (>40x20px)
        Use QPainter antialiasing for text only.
        """

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Hit-test against last computed layout, emit node_selected."""
```

Color constants (define at module level):

```python
CHANGE_TYPE_COLORS: dict[ChangeType | None, QColor] = {
    ChangeType.CREATED:     QColor("#4caf50"),
    ChangeType.MODIFIED:    QColor("#ff9800"),
    ChangeType.DELETED:     QColor("#f44336"),
    ChangeType.RENAMED:     QColor("#2196f3"),
    ChangeType.PERMISSIONS: QColor("#9e9e9e"),
    None:                   QColor("#424242"),  # intermediate directory
}
```

### `ui/tree_view.py`

Responsibility: display the `DiffTree` as a collapsible `QTreeView` with columns: Name, Change, Size.

```python
class DiffTreeModel(QAbstractItemModel):
    """
    Standard QAbstractItemModel wrapping DiffTree.
    Column 0: node name, decorated with change-type icon (use QStyle standard icons or colored square)
    Column 1: change type string
    Column 2: human-readable size (use 'shared' for intermediate dirs)
    """

class DiffTreeView(QTreeView):
    node_selected = pyqtSignal(str)   # emits full_path on selection change
```

### `ui/snapshot_selector.py`

Responsibility: allow the user to select base and new snapshots, and a filesystem root to enumerate from.

```python
class SnapshotSelector(QWidget):
    diff_requested = pyqtSignal(str, str)  # (base_path, new_path)

    # Contains:
    # - QLineEdit for filesystem root, with a browse button
    # - Two QComboBox widgets populated by list_subvolumes()
    # - "Compare" QPushButton that emits diff_requested
    # - Status label for errors (PermissionError, no snapshots found, etc.)
```

### `ui/main_window.py`

Responsibility: wire all widgets together.

Layout:

```
┌─────────────────────────────────────────────────────┐
│  SnapshotSelector (top bar)                         │
├───────────────────┬─────────────────────────────────┤
│  DiffTreeView     │  TreemapWidget                  │
│  (left panel)     │  (right panel)                  │
└───────────────────┴─────────────────────────────────┘
│  Status bar: total changes, total size delta        │
└─────────────────────────────────────────────────────┘
```

Use `QSplitter` for the two panels. Persist splitter position in `QSettings`.

**Synchronization:** selecting a node in either panel selects the corresponding node in the other. Implement via the `node_selected` signals on both widgets; use a flag to prevent signal loops.

**Diff workflow:**

```
SnapshotSelector.diff_requested
  → run compute_diff() in QThread (never block the main thread)
  → on completion: build DiffTree, call enrich(), update both widgets
  → on error: show QMessageBox with error detail
```

---

## Error Handling Policy

- All `btrfs` CLI calls must check return code and stderr; raise typed exceptions (`DiffError`, `SubvolumeListError`, `PermissionError`) with the stderr content included in the message.
- UI must never crash on a subprocess error; catch at the `QThread` boundary and display via status bar or `QMessageBox`.
- File stat failures during enrichment are silent (see enrichment spec above).
- If `btrfs` is not found on `PATH`, raise `RuntimeError` with an install hint.

---

## Testing

- `test_diff_parser.py`: test the `btrfs receive --dump` line parser against fixture strings covering all operation tokens, including edge cases (paths with spaces, paths with Unicode, rename lines).
- `test_diff_tree.py`: test `DiffTree.build()` with known `ChangeRecord` lists; assert correct parent-child structure, `total_size` aggregation, and rename expansion.
- `test_treemap_layout.py`: test `squarify()` with known inputs; assert all rects fit within the bounding rect, no overlaps, area proportionality within 1% tolerance.

Tests must not invoke any `btrfs` CLI commands or require root. Use `pytest`. Add to `pyproject.toml`:

```toml
[dependency-groups]
dev = ["pytest>=8"]
```

Run with:
```bash
uv run pytest
```

---

## Constraints and Coding Standards

- Python 3.11+. Use `dataclasses`, `enum`, and type hints throughout.
- No global mutable state. All state lives in widget instances or is passed explicitly.
- All subprocess calls go through `utils/subprocess.py`; nothing else calls `subprocess` directly.
- `btrfs/` and `model/` layers must have zero PyQt6 imports. They are pure logic.
- `squarify()` must be a pure function with no side effects.
- Format with `ruff format`, lint with `ruff check`. Add to `pyproject.toml`:
  ```toml
  [tool.ruff]
  target-version = "uv3.11"
  line-length = 100
  ```
- Do not use `os.system` anywhere.
- Long-running operations (`compute_diff`, `enrich`) must run in a `QThread` subclass with a `finished = pyqtSignal(object)` and `error = pyqtSignal(Exception)` interface.

---

## Invocation

```bash
# install and run
uv run snapdiff

# requires root for btrfs send
sudo uv run snapdiff
```

The app must handle being run without root gracefully: show a clear error in the UI rather than crashing.
