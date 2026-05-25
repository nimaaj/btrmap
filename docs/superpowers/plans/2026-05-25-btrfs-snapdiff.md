# btrfs-snapdiff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a PyQt6 desktop GUI that diffs two read-only btrfs snapshots and displays the result as a synchronized tree view + squarified treemap.

**Architecture:** Three strict layers: `btrfs/` (CLI wrappers, zero PyQt6) → `model/` (pure data structures, zero PyQt6) → `ui/` (PyQt6 widgets only). All subprocess calls go through `utils/subprocess.py`. Long-running ops run in `QThread` subclasses with `finished/error` signals.

**Tech Stack:** Python 3.11+, PyQt6 ≥ 6.6, pytest ≥ 8, ruff — managed with `uv`.

---

## File Map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Project config, deps, ruff config, entry point |
| `src/snapdiff/__init__.py` | Package marker |
| `src/snapdiff/main.py` | `QApplication` entry point |
| `src/snapdiff/utils/__init__.py` | Package marker |
| `src/snapdiff/utils/subprocess.py` | `run()` and `pipe()` — only place that imports `subprocess` |
| `src/snapdiff/btrfs/__init__.py` | Package marker |
| `src/snapdiff/btrfs/diff.py` | `ChangeType`, `ChangeRecord`, `_parse_line()`, `compute_diff()` |
| `src/snapdiff/btrfs/subvolumes.py` | `Subvolume`, `list_subvolumes()` |
| `src/snapdiff/model/__init__.py` | Package marker |
| `src/snapdiff/model/diff_tree.py` | `DiffNode`, `DiffTree` |
| `src/snapdiff/model/enrichment.py` | `enrich()` — stat files to populate `size_bytes` |
| `src/snapdiff/ui/__init__.py` | Package marker |
| `src/snapdiff/ui/treemap.py` | `Rect`, `squarify()`, `CHANGE_TYPE_COLORS`, `TreemapWidget` |
| `src/snapdiff/ui/tree_view.py` | `DiffTreeModel`, `DiffTreeView` |
| `src/snapdiff/ui/snapshot_selector.py` | `SnapshotSelector` widget |
| `src/snapdiff/ui/main_window.py` | `DiffWorker` (QThread), `MainWindow` |
| `tests/conftest.py` | Shared pytest fixtures |
| `tests/test_diff_parser.py` | Unit tests for `_parse_line()` and `_deduplicate()` |
| `tests/test_diff_tree.py` | Unit tests for `DiffTree.build()`, `total_size`, rename expansion |
| `tests/test_treemap_layout.py` | Unit tests for `squarify()` geometric invariants |

---

## Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/snapdiff/__init__.py`, `src/snapdiff/btrfs/__init__.py`, `src/snapdiff/model/__init__.py`, `src/snapdiff/ui/__init__.py`, `src/snapdiff/utils/__init__.py`
- Create: `tests/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: Create `pyproject.toml`**

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

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create all `__init__.py` files**

```bash
mkdir -p src/snapdiff/{btrfs,model,ui,utils} tests
touch src/snapdiff/__init__.py \
      src/snapdiff/btrfs/__init__.py \
      src/snapdiff/model/__init__.py \
      src/snapdiff/ui/__init__.py \
      src/snapdiff/utils/__init__.py \
      tests/__init__.py
```

- [ ] **Step 3: Create `tests/conftest.py`**

```python
# tests/conftest.py
# Shared fixtures added here as the test suite grows.
```

- [ ] **Step 4: Install dependencies and verify**

```bash
uv sync --dev
uv run python -c "import PyQt6; print('PyQt6 OK')"
uv run pytest --collect-only
```

Expected: `no tests ran`, no errors.

- [ ] **Step 5: Init git and commit**

```bash
git init
git add .
git commit -m "chore: scaffold project structure"
```

---

## Task 2: `utils/subprocess.py`

**Files:**
- Create: `src/snapdiff/utils/subprocess.py`

No unit tests — this module is mocked at the boundary in all other tests.

- [ ] **Step 1: Write `utils/subprocess.py`**

```python
# src/snapdiff/utils/subprocess.py
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence


def _check_exists(cmd: str) -> None:
    if shutil.which(cmd) is None:
        raise RuntimeError(
            f"Command not found: {cmd!r}. "
            "Install btrfs-progs (Arch Linux: sudo pacman -S btrfs-progs)."
        )


def run(args: Sequence[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command; return CompletedProcess. Raises RuntimeError if binary not found."""
    _check_exists(args[0])
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def pipe(
    first: Sequence[str],
    second: Sequence[str],
    *,
    timeout: float | None = None,
) -> tuple[str, int, str, int]:
    """
    Run `first | second`.
    Returns (stdout, first_returncode, first_stderr, second_returncode).
    Raises RuntimeError if either binary is not found.
    """
    _check_exists(first[0])
    _check_exists(second[0])

    proc1 = subprocess.Popen(list(first), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc2 = subprocess.Popen(
        list(second),
        stdin=proc1.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    proc1.stdout.close()  # allow proc1 to receive SIGPIPE if proc2 exits early
    stdout, stderr2 = proc2.communicate(timeout=timeout)
    _, stderr1_bytes = proc1.communicate()
    return stdout, proc1.returncode, stderr1_bytes.decode(), proc2.returncode
```

- [ ] **Step 2: Commit**

```bash
git add src/snapdiff/utils/subprocess.py
git commit -m "feat: add subprocess utility wrapper"
```

---

## Task 3: `btrfs/diff.py` — Types and Parser

**Files:**
- Create: `src/snapdiff/btrfs/diff.py` (types + `_parse_line` + `_deduplicate`)
- Create: `tests/test_diff_parser.py`

- [ ] **Step 1: Write failing tests for `_parse_line`**

```python
# tests/test_diff_parser.py
from __future__ import annotations

import pytest

from snapdiff.btrfs.diff import ChangeRecord, ChangeType, _deduplicate, _parse_line


@pytest.mark.parametrize(
    "line,expected",
    [
        # MODIFIED
        (
            "write                   path foo/bar.txt offset 0 len 1024",
            ChangeRecord(ChangeType.MODIFIED, "foo/bar.txt"),
        ),
        (
            "truncate                path foo/bar.txt size 0",
            ChangeRecord(ChangeType.MODIFIED, "foo/bar.txt"),
        ),
        # CREATED
        (
            "mkfile                  path new_file.py",
            ChangeRecord(ChangeType.CREATED, "new_file.py"),
        ),
        (
            "mkdir                   path some/directory",
            ChangeRecord(ChangeType.CREATED, "some/directory"),
        ),
        (
            "mksock                  path run/app.sock",
            ChangeRecord(ChangeType.CREATED, "run/app.sock"),
        ),
        (
            "mkfifo                  path var/pipe",
            ChangeRecord(ChangeType.CREATED, "var/pipe"),
        ),
        (
            "symlink                 path link/target dest /original",
            ChangeRecord(ChangeType.CREATED, "link/target"),
        ),
        (
            "link                    path link/target dest /original",
            ChangeRecord(ChangeType.CREATED, "link/target"),
        ),
        # DELETED
        (
            "unlink                  path old_file.txt",
            ChangeRecord(ChangeType.DELETED, "old_file.txt"),
        ),
        (
            "rmdir                   path empty_dir",
            ChangeRecord(ChangeType.DELETED, "empty_dir"),
        ),
        # RENAMED — emits single record with old_path
        (
            "rename                  path oldname.txt -> newname.txt",
            ChangeRecord(ChangeType.RENAMED, "newname.txt", old_path="oldname.txt"),
        ),
        # PERMISSIONS
        (
            "chmod                   path file.sh mode 0755",
            ChangeRecord(ChangeType.PERMISSIONS, "file.sh"),
        ),
        (
            "chown                   path file.txt uid 1000 gid 1000",
            ChangeRecord(ChangeType.PERMISSIONS, "file.txt"),
        ),
        (
            "utimes                  path file.txt atime 2024-01-01T00:00:00 mtime 2024-01-01T00:00:00 ctime 2024-01-01T00:00:00",
            ChangeRecord(ChangeType.PERMISSIONS, "file.txt"),
        ),
        (
            "set_xattr               path file.txt name user.comment data hello len 5",
            ChangeRecord(ChangeType.PERMISSIONS, "file.txt"),
        ),
        # Paths with spaces
        (
            "mkfile                  path dir/file with spaces.txt",
            ChangeRecord(ChangeType.CREATED, "dir/file with spaces.txt"),
        ),
        # Rename with spaces in path
        (
            "rename                  path old name.txt -> new name.txt",
            ChangeRecord(ChangeType.RENAMED, "new name.txt", old_path="old name.txt"),
        ),
        # Paths with Unicode
        (
            "write                   path 日本語/ファイル.txt offset 0 len 10",
            ChangeRecord(ChangeType.MODIFIED, "日本語/ファイル.txt"),
        ),
        (
            "mkfile                  path données/résumé.pdf",
            ChangeRecord(ChangeType.CREATED, "données/résumé.pdf"),
        ),
        # Unknown tokens → None
        ("at                      root .", None),
        ("subvol                  path subvol_name", None),
        ("clone                   path foo offset 0 len 4096 from bar clone_offset 0", None),
        # Empty line → None
        ("", None),
        ("   ", None),
    ],
)
def test_parse_line(line: str, expected: ChangeRecord | None) -> None:
    assert _parse_line(line) == expected


def test_deduplicate_permissions_and_modified_keeps_modified() -> None:
    records = [
        ChangeRecord(ChangeType.PERMISSIONS, "file.txt"),
        ChangeRecord(ChangeType.MODIFIED, "file.txt"),
    ]
    result = _deduplicate(records)
    assert len(result) == 1
    assert result[0].change_type == ChangeType.MODIFIED


def test_deduplicate_modified_then_permissions_keeps_modified() -> None:
    records = [
        ChangeRecord(ChangeType.MODIFIED, "file.txt"),
        ChangeRecord(ChangeType.PERMISSIONS, "file.txt"),
    ]
    result = _deduplicate(records)
    assert len(result) == 1
    assert result[0].change_type == ChangeType.MODIFIED


def test_deduplicate_preserves_unrelated_records() -> None:
    records = [
        ChangeRecord(ChangeType.CREATED, "a.txt"),
        ChangeRecord(ChangeType.DELETED, "b.txt"),
    ]
    result = _deduplicate(records)
    assert len(result) == 2
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_diff_parser.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` (module doesn't exist yet).

- [ ] **Step 3: Implement types and `_parse_line` in `btrfs/diff.py`**

```python
# src/snapdiff/btrfs/diff.py
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class ChangeType(Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    PERMISSIONS = "permissions"


@dataclass
class ChangeRecord:
    change_type: ChangeType
    path: str
    old_path: str | None = field(default=None)


class DiffError(Exception):
    pass


_TOKEN_MAP: dict[str, ChangeType] = {
    "write": ChangeType.MODIFIED,
    "truncate": ChangeType.MODIFIED,
    "mkfile": ChangeType.CREATED,
    "mkdir": ChangeType.CREATED,
    "mksock": ChangeType.CREATED,
    "mkfifo": ChangeType.CREATED,
    "symlink": ChangeType.CREATED,
    "link": ChangeType.CREATED,
    "unlink": ChangeType.DELETED,
    "rmdir": ChangeType.DELETED,
    "rename": ChangeType.RENAMED,
    "chmod": ChangeType.PERMISSIONS,
    "chown": ChangeType.PERMISSIONS,
    "utimes": ChangeType.PERMISSIONS,
    "set_xattr": ChangeType.PERMISSIONS,
}

# Subsequent field keywords that terminate a path in btrfs receive --dump output.
_TRAILING_FIELD_RE = re.compile(
    r"\s+(?:offset|len|dest|mode|dev|uid|gid|atime|mtime|ctime|size|name|data|"
    r"clone_offset|from|root)\s+"
)


def _extract_path(text: str) -> str:
    """Strip trailing 'keyword value' pairs from a path string."""
    m = _TRAILING_FIELD_RE.search(text)
    if m:
        return text[: m.start()].strip()
    return text.strip()


def _parse_line(line: str) -> ChangeRecord | None:
    """Parse one line from `btrfs receive --dump` output. Returns None for unrecognised lines."""
    line = line.strip()
    if not line:
        return None

    parts = line.split(None, 1)
    if len(parts) < 2:
        return None

    token, rest = parts[0], parts[1].lstrip()
    change_type = _TOKEN_MAP.get(token)
    if change_type is None:
        return None

    if not rest.startswith("path "):
        return None

    path_and_rest = rest[5:]  # strip leading "path "

    if change_type == ChangeType.RENAMED:
        if " -> " not in path_and_rest:
            return None
        old_path, new_path = path_and_rest.split(" -> ", 1)
        return ChangeRecord(ChangeType.RENAMED, new_path.strip(), old_path=old_path.strip())

    return ChangeRecord(change_type, _extract_path(path_and_rest))


def _deduplicate(records: list[ChangeRecord]) -> list[ChangeRecord]:
    """If a path has both PERMISSIONS and MODIFIED records, keep only MODIFIED."""
    by_path: dict[str, ChangeRecord] = {}
    for record in records:
        key = record.path
        if key not in by_path:
            by_path[key] = record
        else:
            existing = by_path[key]
            if (
                existing.change_type == ChangeType.PERMISSIONS
                and record.change_type == ChangeType.MODIFIED
            ):
                by_path[key] = record
            # If existing is MODIFIED and new is PERMISSIONS, keep MODIFIED (no-op).
    return list(by_path.values())
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_diff_parser.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/snapdiff/btrfs/diff.py tests/test_diff_parser.py
git commit -m "feat: add btrfs diff parser with TDD"
```

---

## Task 4: `btrfs/diff.py` — `compute_diff()`

**Files:**
- Modify: `src/snapdiff/btrfs/diff.py`
- Modify: `tests/test_diff_parser.py`

- [ ] **Step 1: Write failing tests for `compute_diff`**

Append to `tests/test_diff_parser.py`:

```python
from unittest.mock import patch

from snapdiff.btrfs.diff import DiffError, compute_diff


def test_compute_diff_parses_output() -> None:
    fake_stdout = (
        "mkfile                  path new_file.txt\n"
        "write                   path new_file.txt offset 0 len 10\n"
        "chmod                   path new_file.txt mode 0644\n"
    )
    with patch("snapdiff.utils.subprocess.pipe", return_value=(fake_stdout, 0, "", 0)):
        records = compute_diff("/snap/base", "/snap/new")
    # chmod + write on same path → deduplicated to MODIFIED only
    assert len(records) == 2
    paths = {r.path for r in records}
    assert paths == {"new_file.txt"}
    types = {r.change_type for r in records}
    assert ChangeType.CREATED in types
    assert ChangeType.MODIFIED in types
    assert ChangeType.PERMISSIONS not in types


def test_compute_diff_raises_diff_error_on_send_failure() -> None:
    with patch("snapdiff.utils.subprocess.pipe", return_value=("", 1, "some btrfs error", 0)):
        with pytest.raises(DiffError, match="btrfs send failed"):
            compute_diff("/snap/base", "/snap/new")


def test_compute_diff_raises_permission_error() -> None:
    with patch(
        "snapdiff.utils.subprocess.pipe",
        return_value=("", 1, "ERROR: Operation not permitted", 0),
    ):
        with pytest.raises(PermissionError, match="root"):
            compute_diff("/snap/base", "/snap/new")


def test_compute_diff_raises_diff_error_on_receive_failure() -> None:
    with patch("snapdiff.utils.subprocess.pipe", return_value=("", 0, "", 1)):
        with pytest.raises(DiffError, match="btrfs receive"):
            compute_diff("/snap/base", "/snap/new")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_diff_parser.py::test_compute_diff_parses_output -v
```

Expected: `ImportError` (`compute_diff` not yet defined).

- [ ] **Step 3: Implement `compute_diff` — append to `btrfs/diff.py`**

```python
from snapdiff.utils import subprocess as sp


def _parse_output(stdout: str) -> list[ChangeRecord]:
    records = []
    for line in stdout.splitlines():
        record = _parse_line(line)
        if record is not None:
            records.append(record)
    return records


def compute_diff(base_snapshot: str, new_snapshot: str) -> list[ChangeRecord]:
    """
    Run `btrfs send --no-data -p <base> <new> | btrfs receive --dump`.
    Returns deduplicated list of ChangeRecord. Raises DiffError, PermissionError,
    or RuntimeError (btrfs not found).
    """
    stdout, send_rc, send_stderr, receive_rc = sp.pipe(
        ["btrfs", "send", "--no-data", "-p", base_snapshot, new_snapshot],
        ["btrfs", "receive", "--dump"],
    )

    if send_rc != 0:
        if "Operation not permitted" in send_stderr or "EPERM" in send_stderr:
            raise PermissionError(
                "btrfs send requires root or CAP_SYS_ADMIN. "
                "Run the application with sudo or grant the appropriate capability."
            )
        raise DiffError(f"btrfs send failed (exit {send_rc}): {send_stderr.strip()}")

    if receive_rc != 0:
        raise DiffError(f"btrfs receive --dump failed (exit {receive_rc})")

    return _deduplicate(_parse_output(stdout))
```

- [ ] **Step 4: Run all parser tests**

```bash
uv run pytest tests/test_diff_parser.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/snapdiff/btrfs/diff.py tests/test_diff_parser.py
git commit -m "feat: implement compute_diff with error handling"
```

---

## Task 5: `btrfs/subvolumes.py`

**Files:**
- Create: `src/snapdiff/btrfs/subvolumes.py`
- Create: `tests/test_subvolumes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_subvolumes.py
from __future__ import annotations

import pytest
from unittest.mock import patch

from snapdiff.btrfs.subvolumes import Subvolume, SubvolumeListError, list_subvolumes


SAMPLE_OUTPUT = """\
ID 256 gen 7 top level 5 parent_uuid -                                    path snapshots/2024-01
ID 257 gen 12 top level 5 parent_uuid 3a9b4c2d-1234-5678-9abc-def012345678 path snapshots/2024-02
ID 258 gen 20 top level 5 parent_uuid -                                    path data/backup
"""


def test_list_subvolumes_parses_output() -> None:
    mock_result = type("R", (), {"returncode": 0, "stdout": SAMPLE_OUTPUT, "stderr": ""})()
    with patch("snapdiff.utils.subprocess.run", return_value=mock_result):
        result = list_subvolumes("/")
    assert len(result) == 3
    assert result[0] == Subvolume(id=256, path="snapshots/2024-01", mount_point=None, is_readonly=True, generation=7)
    assert result[1].path == "snapshots/2024-02"
    assert result[2].id == 258


def test_list_subvolumes_raises_on_nonzero_exit() -> None:
    mock_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "No such file"})()
    with patch("snapdiff.utils.subprocess.run", return_value=mock_result):
        with pytest.raises(SubvolumeListError, match="No such file"):
            list_subvolumes("/nonexistent")


def test_list_subvolumes_skips_unparseable_lines() -> None:
    output = "garbage line\nID 256 gen 7 top level 5 parent_uuid - path snap\n"
    mock_result = type("R", (), {"returncode": 0, "stdout": output, "stderr": ""})()
    with patch("snapdiff.utils.subprocess.run", return_value=mock_result):
        result = list_subvolumes("/")
    assert len(result) == 1
    assert result[0].path == "snap"
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_subvolumes.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `btrfs/subvolumes.py`**

```python
# src/snapdiff/btrfs/subvolumes.py
from __future__ import annotations

import re
from dataclasses import dataclass

from snapdiff.utils import subprocess as sp

# btrfs subvolume list -rpo output format:
# ID <id> gen <gen> top level <lvl> [parent_uuid <uuid>] path <path>
_SUBVOL_RE = re.compile(
    r"ID\s+(\d+)\s+gen\s+(\d+)\s+top level\s+\d+\s+(?:parent_uuid\s+\S+\s+)?path\s+(.+)$"
)


@dataclass(frozen=True)
class Subvolume:
    id: int
    path: str          # path relative to filesystem root
    mount_point: str | None
    is_readonly: bool
    generation: int


class SubvolumeListError(Exception):
    pass


def list_subvolumes(fs_path: str) -> list[Subvolume]:
    """
    Run `btrfs subvolume list -rpo <fs_path>` and parse stdout.
    Returns only read-only subvolumes (guaranteed by the -r flag).
    Raises SubvolumeListError on non-zero exit.
    Raises RuntimeError if btrfs is not on PATH.
    """
    result = sp.run(["btrfs", "subvolume", "list", "-rpo", fs_path])
    if result.returncode != 0:
        raise SubvolumeListError(
            f"btrfs subvolume list failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return _parse_output(result.stdout)


def _parse_output(stdout: str) -> list[Subvolume]:
    subvolumes = []
    for line in stdout.splitlines():
        m = _SUBVOL_RE.match(line.strip())
        if m:
            subvolumes.append(
                Subvolume(
                    id=int(m.group(1)),
                    generation=int(m.group(2)),
                    path=m.group(3).strip(),
                    mount_point=None,
                    is_readonly=True,  # -r flag guarantees read-only
                )
            )
    return subvolumes
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_subvolumes.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/snapdiff/btrfs/subvolumes.py tests/test_subvolumes.py
git commit -m "feat: add subvolume enumeration"
```

---

## Task 6: `model/diff_tree.py`

**Files:**
- Create: `src/snapdiff/model/diff_tree.py`
- Create: `tests/test_diff_tree.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_diff_tree.py
from __future__ import annotations

import pytest

from snapdiff.btrfs.diff import ChangeRecord, ChangeType
from snapdiff.model.diff_tree import DiffNode, DiffTree


def _records(*args: tuple[ChangeType, str, str | None]) -> list[ChangeRecord]:
    return [ChangeRecord(ct, path, old_path) for ct, path, old_path in args]


def test_build_single_leaf() -> None:
    records = [ChangeRecord(ChangeType.CREATED, "file.txt")]
    tree = DiffTree.build(records)
    node = tree.find("file.txt")
    assert node is not None
    assert node.name == "file.txt"
    assert node.change_type == ChangeType.CREATED
    assert node.is_leaf


def test_build_nested_path_creates_intermediate_nodes() -> None:
    records = [ChangeRecord(ChangeType.MODIFIED, "src/main.py")]
    tree = DiffTree.build(records)
    src = tree.find("src")
    assert src is not None
    assert src.change_type is None  # intermediate dir
    assert not src.is_leaf
    main_py = tree.find("src/main.py")
    assert main_py is not None
    assert main_py.change_type == ChangeType.MODIFIED
    assert main_py.is_leaf


def test_build_renamed_expands_to_two_leaves() -> None:
    records = [ChangeRecord(ChangeType.RENAMED, "new.txt", old_path="old.txt")]
    tree = DiffTree.build(records)
    old_node = tree.find("old.txt")
    new_node = tree.find("new.txt")
    assert old_node is not None
    assert old_node.change_type == ChangeType.DELETED
    assert new_node is not None
    assert new_node.change_type == ChangeType.CREATED


def test_total_size_leaf() -> None:
    records = [ChangeRecord(ChangeType.CREATED, "a.txt")]
    tree = DiffTree.build(records)
    node = tree.find("a.txt")
    assert node is not None
    node.size_bytes = 1024
    assert node.total_size == 1024


def test_total_size_aggregates_across_children() -> None:
    records = [
        ChangeRecord(ChangeType.CREATED, "dir/a.txt"),
        ChangeRecord(ChangeType.MODIFIED, "dir/b.txt"),
    ]
    tree = DiffTree.build(records)
    a = tree.find("dir/a.txt")
    b = tree.find("dir/b.txt")
    a.size_bytes = 100
    b.size_bytes = 200
    dir_node = tree.find("dir")
    assert dir_node.total_size == 300


def test_iter_leaves_returns_only_leaves() -> None:
    records = [
        ChangeRecord(ChangeType.CREATED, "dir/a.txt"),
        ChangeRecord(ChangeType.MODIFIED, "dir/b.txt"),
        ChangeRecord(ChangeType.DELETED, "root_file.txt"),
    ]
    tree = DiffTree.build(records)
    leaves = list(tree.iter_leaves())
    assert len(leaves) == 3
    assert all(n.is_leaf for n in leaves)
    paths = {n.full_path for n in leaves}
    assert paths == {"dir/a.txt", "dir/b.txt", "root_file.txt"}


def test_find_returns_none_for_missing_path() -> None:
    tree = DiffTree.build([ChangeRecord(ChangeType.CREATED, "a.txt")])
    assert tree.find("nonexistent.txt") is None


def test_find_root() -> None:
    tree = DiffTree.build([ChangeRecord(ChangeType.CREATED, "a.txt")])
    root = tree.find("")
    assert root is tree.root
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_diff_tree.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `model/diff_tree.py`**

```python
# src/snapdiff/model/diff_tree.py
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from snapdiff.btrfs.diff import ChangeRecord, ChangeType


@dataclass
class DiffNode:
    name: str
    full_path: str
    change_type: ChangeType | None
    size_bytes: int
    children: dict[str, "DiffNode"] = field(default_factory=dict)

    @property
    def total_size(self) -> int:
        """Recursive sum of size_bytes across all leaf descendants."""
        if self.is_leaf:
            return self.size_bytes
        return sum(child.total_size for child in self.children.values())

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0


@dataclass
class DiffTree:
    root: DiffNode

    @classmethod
    def build(cls, records: list[ChangeRecord]) -> "DiffTree":
        root = DiffNode(name="", full_path="", change_type=None, size_bytes=0)
        for record in records:
            if record.change_type == ChangeType.RENAMED:
                if record.old_path:
                    cls._insert(root, ChangeRecord(ChangeType.DELETED, record.old_path))
                cls._insert(root, ChangeRecord(ChangeType.CREATED, record.path))
            else:
                cls._insert(root, record)
        return cls(root=root)

    @staticmethod
    def _insert(root: DiffNode, record: ChangeRecord) -> None:
        parts = record.path.split("/")
        node = root
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            full_path = "/".join(parts[: i + 1])
            if part not in node.children:
                node.children[part] = DiffNode(
                    name=part,
                    full_path=full_path,
                    change_type=record.change_type if is_last else None,
                    size_bytes=0,
                )
            node = node.children[part]

    def iter_leaves(self) -> Iterator[DiffNode]:
        def _iter(node: DiffNode) -> Iterator[DiffNode]:
            if node.is_leaf:
                yield node
            else:
                for child in node.children.values():
                    yield from _iter(child)

        return _iter(self.root)

    def find(self, full_path: str) -> DiffNode | None:
        if not full_path:
            return self.root
        node = self.root
        for part in full_path.split("/"):
            if part not in node.children:
                return None
            node = node.children[part]
        return node
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_diff_tree.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/snapdiff/model/diff_tree.py tests/test_diff_tree.py
git commit -m "feat: implement DiffNode and DiffTree"
```

---

## Task 7: `model/enrichment.py`

**Files:**
- Create: `src/snapdiff/model/enrichment.py`
- Create: `tests/test_enrichment.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_enrichment.py
from __future__ import annotations

from unittest.mock import MagicMock, patch

from snapdiff.btrfs.diff import ChangeRecord, ChangeType
from snapdiff.model.diff_tree import DiffTree
from snapdiff.model.enrichment import enrich


def _make_tree(*records: ChangeRecord) -> DiffTree:
    return DiffTree.build(list(records))


def _stat(size: int) -> MagicMock:
    m = MagicMock()
    m.st_size = size
    return m


def test_enrich_created_uses_new_snapshot() -> None:
    tree = _make_tree(ChangeRecord(ChangeType.CREATED, "new_file.txt"))
    with patch("os.stat", return_value=_stat(1024)) as mock_stat:
        enrich(tree, "/new", "/base")
        mock_stat.assert_called_once_with("/new/new_file.txt")
    assert tree.find("new_file.txt").size_bytes == 1024


def test_enrich_deleted_uses_base_snapshot() -> None:
    tree = _make_tree(ChangeRecord(ChangeType.DELETED, "old_file.txt"))
    with patch("os.stat", return_value=_stat(512)) as mock_stat:
        enrich(tree, "/new", "/base")
        mock_stat.assert_called_once_with("/base/old_file.txt")
    assert tree.find("old_file.txt").size_bytes == 512


def test_enrich_modified_uses_new_snapshot() -> None:
    tree = _make_tree(ChangeRecord(ChangeType.MODIFIED, "changed.txt"))
    with patch("os.stat", return_value=_stat(2048)) as mock_stat:
        enrich(tree, "/new", "/base")
        mock_stat.assert_called_once_with("/new/changed.txt")
    assert tree.find("changed.txt").size_bytes == 2048


def test_enrich_renamed_uses_new_snapshot_for_new_path() -> None:
    # RENAMED expands to DELETED (old) + CREATED (new)
    tree = _make_tree(ChangeRecord(ChangeType.RENAMED, "b.txt", old_path="a.txt"))
    sizes = {"a.txt": 100, "b.txt": 200}

    def fake_stat(path: str) -> MagicMock:
        for name, size in sizes.items():
            if path.endswith(name):
                return _stat(size)
        raise OSError("not found")

    with patch("os.stat", side_effect=fake_stat):
        enrich(tree, "/new", "/base")

    assert tree.find("a.txt").size_bytes == 100  # DELETED → base
    assert tree.find("b.txt").size_bytes == 200  # CREATED → new


def test_enrich_stat_failure_is_silent() -> None:
    tree = _make_tree(ChangeRecord(ChangeType.CREATED, "missing.sock"))
    with patch("os.stat", side_effect=OSError("not a regular file")):
        enrich(tree, "/new", "/base")  # must not raise
    assert tree.find("missing.sock").size_bytes == 0
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_enrichment.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `model/enrichment.py`**

```python
# src/snapdiff/model/enrichment.py
from __future__ import annotations

import os

from snapdiff.btrfs.diff import ChangeType
from snapdiff.model.diff_tree import DiffNode, DiffTree


def enrich(tree: DiffTree, new_snapshot_mount: str, base_snapshot_mount: str) -> None:
    """
    Populate size_bytes on each leaf node by stat-ing the file in the appropriate snapshot.
    Mutates the tree in place. Stat failures are silently ignored (size_bytes stays 0).
    """
    for node in tree.iter_leaves():
        _stat_node(node, new_snapshot_mount, base_snapshot_mount)


def _stat_node(node: DiffNode, new_mount: str, base_mount: str) -> None:
    mount = base_mount if node.change_type == ChangeType.DELETED else new_mount
    path = os.path.join(mount, node.full_path)
    try:
        node.size_bytes = os.stat(path).st_size
    except OSError:
        node.size_bytes = 0
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_enrichment.py -v
```

Expected: all green.

- [ ] **Step 5: Run the full test suite**

```bash
uv run pytest -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/snapdiff/model/enrichment.py tests/test_enrichment.py
git commit -m "feat: implement file size enrichment"
```

---

## Task 8: `ui/treemap.py` — `squarify()` Pure Function

**Files:**
- Create: `src/snapdiff/ui/treemap.py` (Rect + squarify + helpers only, no widget yet)
- Create: `tests/test_treemap_layout.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_treemap_layout.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `Rect`, `squarify`, and helpers in `ui/treemap.py`**

```python
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
```

- [ ] **Step 4: Run treemap layout tests**

```bash
uv run pytest tests/test_treemap_layout.py -v
```

Expected: all green.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/snapdiff/ui/treemap.py tests/test_treemap_layout.py
git commit -m "feat: implement squarified treemap layout algorithm"
```

---

## Task 9: `ui/treemap.py` — `TreemapWidget`

**Files:**
- Modify: `src/snapdiff/ui/treemap.py` (append widget code after the pure functions)

No unit tests — widget painting is verified visually in the smoke test (Task 13).

- [ ] **Step 1: Append widget code to `ui/treemap.py`**

```python
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
```

- [ ] **Step 2: Verify existing tests still pass (no regressions)**

```bash
uv run pytest -v
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add src/snapdiff/ui/treemap.py
git commit -m "feat: add TreemapWidget with paint and hit-test"
```

---

## Task 10: `ui/tree_view.py`

**Files:**
- Create: `src/snapdiff/ui/tree_view.py`

- [ ] **Step 1: Create `ui/tree_view.py`**

```python
# src/snapdiff/ui/tree_view.py
from __future__ import annotations

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHeaderView, QTreeView

from snapdiff.btrfs.diff import ChangeType
from snapdiff.model.diff_tree import DiffNode, DiffTree

_CHANGE_COLORS: dict[ChangeType, QColor] = {
    ChangeType.CREATED:     QColor("#4caf50"),
    ChangeType.MODIFIED:    QColor("#ff9800"),
    ChangeType.DELETED:     QColor("#f44336"),
    ChangeType.RENAMED:     QColor("#2196f3"),
    ChangeType.PERMISSIONS: QColor("#9e9e9e"),
}


def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size} {unit}"
        size //= 1024
    return f"{size} TB"


class DiffTreeModel(QAbstractItemModel):
    HEADERS = ["Name", "Change", "Size"]

    def __init__(self, tree: DiffTree, parent=None) -> None:
        super().__init__(parent)
        self._tree = tree
        # Maps full_path → parent DiffNode (None for root's children)
        self._parent_map: dict[str, DiffNode | None] = {}
        # Maps full_path → row index within its parent
        self._row_map: dict[str, int] = {}
        self._build_maps(tree.root, None, 0)

    def _build_maps(self, node: DiffNode, parent: DiffNode | None, row: int) -> None:
        self._parent_map[node.full_path] = parent
        self._row_map[node.full_path] = row
        for i, child in enumerate(node.children.values()):
            self._build_maps(child, node, i)

    def _node(self, index: QModelIndex) -> DiffNode:
        if not index.isValid():
            return self._tree.root
        return index.internalPointer()  # type: ignore[return-value]

    # ── QAbstractItemModel interface ──────────────────────────────────────────

    def index(self, row: int, col: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        parent_node = self._node(parent)
        children = list(parent_node.children.values())
        if 0 <= row < len(children):
            return self.createIndex(row, col, children[row])
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:  # type: ignore[override]
        if not index.isValid():
            return QModelIndex()
        node: DiffNode = index.internalPointer()  # type: ignore[assignment]
        parent_node = self._parent_map.get(node.full_path)
        if parent_node is None:
            return QModelIndex()
        row = self._row_map.get(parent_node.full_path, 0)
        return self.createIndex(row, 0, parent_node)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._node(parent).children)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 3

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node: DiffNode = index.internalPointer()  # type: ignore[assignment]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return node.name
            if col == 1:
                return node.change_type.value if node.change_type else ""
            if col == 2:
                if node.is_leaf:
                    return _fmt_size(node.size_bytes)
                return f"({_fmt_size(node.total_size)} total)"

        if role == Qt.ItemDataRole.ForegroundRole:
            if node.change_type is not None:
                return _CHANGE_COLORS.get(node.change_type)

        return None

    # ── Path lookup ───────────────────────────────────────────────────────────

    def index_for_path(self, full_path: str) -> QModelIndex:
        node = self._tree.find(full_path)
        if node is None or node is self._tree.root:
            return QModelIndex()
        row = self._row_map.get(node.full_path, 0)
        return self.createIndex(row, 0, node)


class DiffTreeView(QTreeView):
    node_selected = pyqtSignal(str)  # emits full_path

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.setAlternatingRowColors(True)

    def set_tree(self, tree: DiffTree) -> None:
        model = DiffTreeModel(tree, self)
        self.setModel(model)
        self.selectionModel().selectionChanged.connect(self._on_selection)
        self.expandToDepth(1)

    def _on_selection(self) -> None:
        indexes = self.selectedIndexes()
        if indexes:
            node: DiffNode = indexes[0].internalPointer()  # type: ignore[assignment]
            self.node_selected.emit(node.full_path)

    def select_node(self, full_path: str) -> None:
        """Select node programmatically (called during cross-widget sync)."""
        model = self.model()
        if not isinstance(model, DiffTreeModel):
            return
        idx = model.index_for_path(full_path)
        if idx.isValid():
            self.blockSignals(True)
            self.setCurrentIndex(idx)
            self.scrollTo(idx)
            self.blockSignals(False)
```

- [ ] **Step 2: Verify tests still pass**

```bash
uv run pytest -v
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add src/snapdiff/ui/tree_view.py
git commit -m "feat: implement DiffTreeModel and DiffTreeView"
```

---

## Task 11: `ui/snapshot_selector.py`

**Files:**
- Create: `src/snapdiff/ui/snapshot_selector.py`

- [ ] **Step 1: Create `ui/snapshot_selector.py`**

```python
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
```

- [ ] **Step 2: Verify tests**

```bash
uv run pytest -v
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add src/snapdiff/ui/snapshot_selector.py
git commit -m "feat: implement SnapshotSelector widget"
```

---

## Task 12: `ui/main_window.py`

**Files:**
- Create: `src/snapdiff/ui/main_window.py`

- [ ] **Step 1: Create `ui/main_window.py`**

```python
# src/snapdiff/ui/main_window.py
from __future__ import annotations

from PyQt6.QtCore import QSettings, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from snapdiff.btrfs.diff import compute_diff
from snapdiff.model.diff_tree import DiffTree
from snapdiff.model.enrichment import enrich
from snapdiff.ui.snapshot_selector import SnapshotSelector
from snapdiff.ui.tree_view import DiffTreeView, _fmt_size
from snapdiff.ui.treemap import TreemapWidget


class DiffWorker(QThread):
    finished: pyqtSignal = pyqtSignal(object)  # emits DiffTree
    error: pyqtSignal = pyqtSignal(Exception)

    def __init__(self, base_path: str, new_path: str, parent=None) -> None:
        super().__init__(parent)
        self._base = base_path
        self._new = new_path

    def run(self) -> None:
        try:
            records = compute_diff(self._base, self._new)
            tree = DiffTree.build(records)
            enrich(tree, self._new, self._base)
            self.finished.emit(tree)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(exc)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("btrfs-snapdiff")
        self.resize(1200, 800)

        self._syncing = False
        self._worker: DiffWorker | None = None

        # Widgets
        self._selector = SnapshotSelector()
        self._tree_view = DiffTreeView()
        self._treemap = TreemapWidget()

        # Layout
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._tree_view)
        self._splitter.addWidget(self._treemap)
        self._splitter.setSizes([400, 800])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._selector)
        layout.addWidget(self._splitter, stretch=1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready — load snapshots and click Compare.")

        # Restore splitter state
        settings = QSettings("btrfs-snapdiff", "main")
        if settings.contains("splitter"):
            self._splitter.restoreState(settings.value("splitter"))  # type: ignore[arg-type]

        # Wire signals
        self._selector.diff_requested.connect(self._start_diff)
        self._tree_view.node_selected.connect(self._on_node_selected)
        self._treemap.node_selected.connect(self._on_node_selected)

    def _start_diff(self, base_path: str, new_path: str) -> None:
        if self._worker and self._worker.isRunning():
            self.statusBar().showMessage("A diff is already running…")
            return
        self.statusBar().showMessage(f"Comparing {base_path} → {new_path}…")
        self._worker = DiffWorker(base_path, new_path, self)
        self._worker.finished.connect(self._on_diff_finished)
        self._worker.error.connect(self._on_diff_error)
        self._worker.start()

    def _on_diff_finished(self, tree: DiffTree) -> None:
        self._tree_view.set_tree(tree)
        self._treemap.set_root(tree.root)
        n_leaves = sum(1 for _ in tree.iter_leaves())
        self.statusBar().showMessage(
            f"{n_leaves} change(s) · {_fmt_size(tree.root.total_size)} total"
        )

    def _on_diff_error(self, exc: Exception) -> None:
        msg = str(exc)
        self.statusBar().showMessage(f"Error: {msg}")
        QMessageBox.critical(self, "Diff failed", msg)

    def _on_node_selected(self, full_path: str) -> None:
        if self._syncing:
            return
        self._syncing = True
        self._tree_view.select_node(full_path)
        self._treemap.select_node(full_path)
        self._syncing = False

    def closeEvent(self, event) -> None:  # type: ignore[override]
        settings = QSettings("btrfs-snapdiff", "main")
        settings.setValue("splitter", self._splitter.saveState())
        super().closeEvent(event)
```

- [ ] **Step 2: Verify tests**

```bash
uv run pytest -v
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add src/snapdiff/ui/main_window.py
git commit -m "feat: implement MainWindow with QThread diff worker"
```

---

## Task 13: `main.py` and Smoke Test

**Files:**
- Create: `src/snapdiff/main.py`

- [ ] **Step 1: Create `main.py`**

```python
# src/snapdiff/main.py
from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from snapdiff.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("btrfs-snapdiff")
    app.setOrganizationName("btrfs-snapdiff")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run full test suite one final time**

```bash
uv run pytest -v
```

Expected: all green.

- [ ] **Step 3: Lint and format**

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

Fix any reported issues, then re-run tests to verify.

- [ ] **Step 4: Smoke test — verify UI launches**

The app requires root for actual btrfs operations, but the UI itself can be tested without:

```bash
# Launch app — should show a window with snapshot selector UI
uv run snapdiff &
sleep 2
# If you see the window open with "Ready — load snapshots and click Compare." in the status bar,
# and no traceback in the terminal, the smoke test passes.
```

If running in a headless environment, verify the import chain at minimum:

```bash
uv run python -c "from snapdiff.ui.main_window import MainWindow; print('All imports OK')"
```

Expected: `All imports OK`.

- [ ] **Step 5: Final commit**

```bash
git add src/snapdiff/main.py
git commit -m "feat: add main entry point — btrfs-snapdiff complete"
```

---

## Self-Review Checklist

Spec coverage verification:

| Spec requirement | Task that covers it |
|---|---|
| `btrfs send --no-data -p <base> <new>` pipe | Task 4 |
| All 15 `btrfs receive --dump` operation tokens | Task 3 |
| PERMISSIONS+MODIFIED dedup rule | Task 3 |
| RENAMED → two leaf nodes | Task 6 |
| `DiffNode.total_size` recursive sum | Task 6 |
| `enrich()` — DELETED uses base, others use new | Task 7 |
| Stat failure silent | Task 7 |
| `squarify()` — Bruls 2000 | Task 8 |
| Zero-size nodes get equal area | Task 8 |
| `min_area` filter | Task 8 |
| `CHANGE_TYPE_COLORS` constants | Task 9 |
| Label only when > 40×20 | Task 9 |
| Cache invalidated by resize or set_root | Task 9 |
| mousePressEvent hit test | Task 9 |
| `DiffTreeModel` — 3 columns | Task 10 |
| `DiffTreeView.select_node` (no loop) | Task 10 |
| `SnapshotSelector` with `diff_requested` signal | Task 11 |
| QSplitter layout, status bar | Task 12 |
| `DiffWorker(QThread)` with `finished`/`error` | Task 12 |
| `_syncing` re-entrancy flag | Task 12 |
| `QSettings` splitter persistence | Task 12 |
| PermissionError → clear UI message | Tasks 4, 12 |
| btrfs not on PATH → RuntimeError | Task 2 |
| `btrfs/` and `model/` zero PyQt6 | All tasks — enforced by import structure |
| All subprocess via `utils/subprocess.py` | Task 2 — only place importing `subprocess` |
| No `os.system` | Confirmed — only `os.stat` and `os.path` used |
| `squarify()` is pure | Task 8 — no side effects, no mutations |
| Tests never invoke btrfs CLI | All test tasks use `unittest.mock.patch` |
| Tests never require root | Confirmed — all subprocess calls are mocked |
