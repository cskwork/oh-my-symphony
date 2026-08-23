"""Windows environment-capability skip helpers shared by test modules.

These probes encode *host capability*, not product behavior:

* ``requires_symlink_privilege`` — creating real symlinks on Windows needs
  Developer Mode or an elevated token (WinError 1314). Tests whose fixtures
  call ``Path.symlink_to``/``os.symlink`` are skipped on hosts without the
  privilege; CI (ubuntu) always has it.
* ``requires_posix_filenames`` — NTFS forbids characters like tab and
  newline in file names, so tests that create such fixture files cannot
  run on win32.

The symlink probe runs once per session and is cached.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import Any, Callable

import pytest

SYMLINK_PRIVILEGE_SKIP_REASON = (
    "symlink privilege not available on this Windows host "
    "(enable Developer Mode or run as administrator)"
)

NTFS_ILLEGAL_FILENAME_SKIP_REASON = (
    "NTFS forbids tab/newline characters in file names; "
    "pathspec behavior with exotic names is covered by Linux CI"
)

_symlink_privilege: bool | None = None


def symlink_privilege_available() -> bool:
    """Probe once whether this process can create a real directory symlink."""
    global _symlink_privilege
    if _symlink_privilege is None:
        probe_dir = tempfile.mkdtemp(prefix="symphony-symlink-probe-")
        try:
            os.symlink(
                probe_dir,
                os.path.join(probe_dir, "probe-link"),
                target_is_directory=True,
            )
            _symlink_privilege = True
        except (OSError, NotImplementedError):
            _symlink_privilege = False
        finally:
            shutil.rmtree(probe_dir, ignore_errors=True)
    return _symlink_privilege


def requires_symlink_privilege(func: Callable[..., Any]) -> Callable[..., Any]:
    """Skip a test when the host cannot create symlinks (WinError 1314)."""
    if symlink_privilege_available():
        return func
    return pytest.mark.skip(reason=SYMLINK_PRIVILEGE_SKIP_REASON)(func)


def requires_posix_filenames(func: Callable[..., Any]) -> Callable[..., Any]:
    """Skip a test whose fixtures need file names NTFS cannot represent."""
    if sys.platform != "win32":
        return func
    return pytest.mark.skip(reason=NTFS_ILLEGAL_FILENAME_SKIP_REASON)(func)
