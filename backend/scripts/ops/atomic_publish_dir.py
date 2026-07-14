#!/usr/bin/env python3
"""Publish a staged directory atomically without replacing an existing target."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import sys


_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_RENAME_EXCL = 0x00000004


class AtomicPublishUnsupported(RuntimeError):
    """Raised when the host has no supported no-replace rename primitive."""


def _raise_rename_error(target: Path) -> None:
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(
            error_number,
            "atomic publish target already exists",
            str(target),
        )
    raise OSError(error_number, os.strerror(error_number), str(target))


def _linux_rename_noreplace(source: Path, target: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise AtomicPublishUnsupported(
            "renameat2(RENAME_NOREPLACE) is unavailable on this Linux host"
        )
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(source),
        _AT_FDCWD,
        os.fsencode(target),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        _raise_rename_error(target)


def _macos_rename_noreplace(source: Path, target: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renamex_np = getattr(libc, "renamex_np", None)
    if renamex_np is None:
        raise AtomicPublishUnsupported(
            "renamex_np(RENAME_EXCL) is unavailable on this macOS host"
        )
    renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex_np.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = renamex_np(
        os.fsencode(source),
        os.fsencode(target),
        _RENAME_EXCL,
    )
    if result != 0:
        _raise_rename_error(target)


def publish_directory_no_replace(source: Path, target: Path) -> None:
    """Atomically rename ``source`` to absent ``target`` without replacement."""

    source = Path(source)
    target = Path(target)
    if not source.is_dir() or source.is_symlink():
        raise ValueError("atomic publish source must be a real directory")
    if source.parent.resolve() != target.parent.resolve():
        raise ValueError("atomic publish source and target must be siblings")

    if sys.platform == "linux":
        _linux_rename_noreplace(source, target)
    elif sys.platform == "darwin":
        _macos_rename_noreplace(source, target)
    else:
        raise AtomicPublishUnsupported(
            f"no no-replace directory rename is supported on {sys.platform}"
        )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: atomic_publish_dir.py <source-dir> <target-dir>", file=sys.stderr)
        return 2
    try:
        publish_directory_no_replace(Path(args[0]), Path(args[1]))
    except (AtomicPublishUnsupported, FileExistsError, OSError, ValueError) as exc:
        print(f"atomic storage publication failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
