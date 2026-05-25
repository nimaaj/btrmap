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
