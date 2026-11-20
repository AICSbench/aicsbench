"""Stable hash over a task's acceptance suite (docs/phase-5/schemas.md).

Files are ordered by POSIX relative path; each contributes
``relative_path + "\\0" + file_bytes + "\\0"``. ``__pycache__`` and
``.pytest_cache`` directories and ``*.pyc`` files are skipped. A task without
a suite hashes to the SHA-256 of the empty string. Editing a suite changes
the hash, which is why editing one requires a new task version.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ai_benchmark.config.loader import TASK_FILENAMES, load_task
from ai_benchmark.config.task import BenchmarkSpec

SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache"})
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def suite_sha256(task: BenchmarkSpec | Path) -> str:
    """Hash the task's acceptance suite.

    Accepts a loaded spec, a task file/directory, or a suite directory itself
    (a directory without a task file is hashed as the suite).
    """
    if isinstance(task, Path):
        if _has_task_file(task):
            return _as_spec_or_suite(task)
        return _hash_suite_dir(task)
    suite = task.acceptance_tests.suite
    if suite is None:
        return EMPTY_SHA256
    root = suite if suite.is_absolute() else (task.source_dir or Path.cwd()) / suite
    return _hash_suite_dir(root)


def _has_task_file(path: Path) -> bool:
    if path.is_file():
        return True
    return path.is_dir() and any((path / name).exists() for name in TASK_FILENAMES)


def _as_spec_or_suite(path: Path) -> str:
    if path.is_dir():
        file = next(candidate for name in TASK_FILENAMES if (candidate := path / name).exists())
        return suite_sha256(load_task(file))
    return suite_sha256(load_task(path))


def _hash_suite_dir(root: Path) -> str:
    if not root.is_dir():
        raise FileNotFoundError(f"acceptance suite directory not found: {root}")

    def onerror(error: OSError) -> None:
        raise error

    files: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        for name in dirnames:
            path = Path(dirpath) / name
            if path.is_symlink():
                raise OSError(f"symlinked directory in acceptance suite: {path}")
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".pyc"):
                continue
            file = Path(dirpath) / name
            files.append((file.relative_to(root).as_posix(), file))
    files.sort(key=lambda entry: entry[0])
    digest = hashlib.sha256()
    for relative, file in files:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
