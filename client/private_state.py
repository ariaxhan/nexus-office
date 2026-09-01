"""Explicit modes for Office-owned state; never follow links during migration."""

from __future__ import annotations

import os
import pathlib
import stat
import tempfile

DIR_MODE = 0o700
FILE_MODE = 0o600


def _absolute(path: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(os.path.abspath(os.fspath(path)))


def _assert_bounded(path: pathlib.Path, anchor: pathlib.Path) -> None:
    """Reject a lexical escape or any symlink from the declared root downward."""
    path, anchor = _absolute(path), _absolute(anchor)
    try:
        relative = path.relative_to(anchor)
    except ValueError as exc:
        raise OSError("private state path escapes its declared root") from exc
    cursor = anchor
    for part in ("", *relative.parts):
        if part:
            cursor /= part
        try:
            mode = cursor.lstat().st_mode
        except FileNotFoundError:
            break
        if stat.S_ISLNK(mode):
            raise OSError("private state path has a symlinked component: %s" % cursor)


def _secure(path: pathlib.Path) -> None:
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise OSError("private state path is a symlink: %s" % path)
        if stat.S_ISDIR(mode):
            os.chmod(path, DIR_MODE, follow_symlinks=False)
        elif stat.S_ISREG(mode):
            os.chmod(path, FILE_MODE, follow_symlinks=False)
    except OSError:
        raise


def ensure_dir(path: pathlib.Path, *, anchor: pathlib.Path | None = None) -> pathlib.Path:
    path = pathlib.Path(path)
    anchor = pathlib.Path(anchor) if anchor is not None else path
    _assert_bounded(path, anchor)
    path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    _assert_bounded(path, anchor)
    _secure(path)
    return path


def migrate_tree(root: pathlib.Path, *, anchor: pathlib.Path | None = None) -> None:
    """Secure only an explicitly supplied Office root; skip every symlink."""
    root = pathlib.Path(root)
    anchor = pathlib.Path(anchor) if anchor is not None else root
    _assert_bounded(root, anchor)
    try:
        mode = root.lstat().st_mode
    except OSError:
        return
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        return
    _secure(root)
    for here, dirs, files in os.walk(root, followlinks=False):
        base = pathlib.Path(here)
        kept = []
        for name in dirs:
            child = base / name
            try:
                mode = child.lstat().st_mode
            except OSError:
                continue
            if stat.S_ISLNK(mode):
                continue
            _secure(child)
            kept.append(name)
        dirs[:] = kept
        for name in files:
            child = base / name
            try:
                mode = child.lstat().st_mode
            except OSError:
                continue
            if stat.S_ISLNK(mode):
                continue
            _secure(child)


def atomic_write_text(path: pathlib.Path, text: str, *, encoding: str = "utf-8",
                      anchor: pathlib.Path | None = None) -> None:
    path = pathlib.Path(path)
    anchor = pathlib.Path(anchor) if anchor is not None else path.parent
    ensure_dir(path.parent, anchor=anchor)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
        os.chmod(tmp, FILE_MODE, follow_symlinks=False)
        os.replace(tmp, path)
        os.chmod(path, FILE_MODE, follow_symlinks=False)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def append_text(path: pathlib.Path, text: str, *, encoding: str = "utf-8",
                anchor: pathlib.Path | None = None) -> None:
    path = pathlib.Path(path)
    anchor = pathlib.Path(anchor) if anchor is not None else path.parent
    ensure_dir(path.parent, anchor=anchor)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, FILE_MODE)
    with os.fdopen(fd, "a", encoding=encoding) as handle:
        handle.write(text)
    os.chmod(path, FILE_MODE, follow_symlinks=False)
