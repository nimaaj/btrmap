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
