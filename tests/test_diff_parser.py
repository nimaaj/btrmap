"""Tests for btrfs receive --dump line parsing: all operation tokens, spaces, Unicode,
both legacy (``path foo/bar``) and modern (``./snapshot/foo/bar key=value``) formats."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from btrmap.btrfs.diff import (
    ChangeRecord,
    ChangeType,
    DiffError,
    _deduplicate,
    _parse_line,
    compute_diff,
)


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
        # ── Real btrfs receive --dump format (no "path " keyword, key=value) ──
        # Paths are prefixed with "./snapshot/" (snapper's subvolume name).
        (
            "write           ./snapshot/usr/lib/foo.so      offset=0 len=4096",
            ChangeRecord(ChangeType.MODIFIED, "usr/lib/foo.so"),
        ),
        (
            "mkfile          ./snapshot/home/user/newfile.txt",
            ChangeRecord(ChangeType.CREATED, "home/user/newfile.txt"),
        ),
        (
            "unlink          ./snapshot/old_file.txt",
            ChangeRecord(ChangeType.DELETED, "old_file.txt"),
        ),
        (
            "utimes          ./snapshot/usr/bin/find         atime=2026-05-26T08:59:59-0400 mtime=2025-07-02T12:26:17-0400 ctime=2025-09-03T13:00:59-0400",
            ChangeRecord(ChangeType.PERMISSIONS, "usr/bin/find"),
        ),
        (
            "chmod           ./snapshot/file.sh              mode=0755",
            ChangeRecord(ChangeType.PERMISSIONS, "file.sh"),
        ),
        (
            "rename          ./snapshot/old.txt              to=./snapshot/new.txt",
            ChangeRecord(ChangeType.RENAMED, "new.txt", old_path="old.txt"),
        ),
        # Snapshot root entry → None (empty path after stripping)
        (
            "utimes          ./snapshot                      atime=2026-05-26T09:00:00-0400 mtime=2026-05-26T09:00:00-0400 ctime=2026-05-26T09:00:00-0400",
            None,
        ),
        # Unknown tokens → None
        ("at                      root .", None),
        ("subvol                  path subvol_name", None),
        ("clone                   path foo offset 0 len 4096 from bar clone_offset 0", None),
        ("snapshot        ./snapshot                      uuid=abc transid=1 parent_uuid=def parent_transid=0", None),
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


def _make_streaming_mock(lines: str, send_rc: int = 0, send_stderr: str = "", receive_rc: int = 0):
    """Return a side_effect for pipe_streaming that feeds lines to the callback."""

    def _fake(first, second, line_callback):
        for line in lines.splitlines(keepends=True):
            line_callback(line)
        return send_rc, send_stderr, receive_rc

    return _fake


def test_compute_diff_parses_output() -> None:
    # Use real btrfs receive --dump format: no "path " keyword, key=value attrs,
    # paths prefixed with "./snapshot/" (snapper's subvolume name).
    fake_stdout = (
        "snapshot        ./snapshot                      uuid=abc transid=1 parent_uuid=def parent_transid=0\n"
        "mkfile          ./snapshot/new_file.txt\n"
        "write           ./snapshot/new_file.txt         offset=0 len=10\n"
        "chmod           ./snapshot/new_file.txt         mode=0644\n"
    )
    with patch(
        "btrmap.utils.subprocess.pipe_streaming",
        side_effect=_make_streaming_mock(fake_stdout),
    ):
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
    with patch(
        "btrmap.utils.subprocess.pipe_streaming",
        side_effect=_make_streaming_mock("", send_rc=1, send_stderr="some btrfs error"),
    ):
        with pytest.raises(DiffError, match="btrfs send failed"):
            compute_diff("/snap/base", "/snap/new")


def test_compute_diff_raises_permission_error() -> None:
    with patch(
        "btrmap.utils.subprocess.pipe_streaming",
        side_effect=_make_streaming_mock("", send_rc=1, send_stderr="ERROR: Operation not permitted"),
    ):
        with pytest.raises(PermissionError, match="root"):
            compute_diff("/snap/base", "/snap/new")


def test_compute_diff_raises_diff_error_on_receive_failure() -> None:
    with patch(
        "btrmap.utils.subprocess.pipe_streaming",
        side_effect=_make_streaming_mock("", receive_rc=1),
    ):
        with pytest.raises(DiffError, match="btrfs receive"):
            compute_diff("/snap/base", "/snap/new")
