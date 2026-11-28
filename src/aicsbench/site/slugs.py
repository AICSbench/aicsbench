"""Page path slugs (docs/phase-5/schemas.md#site-layout).

A slug depends only on its value, never on which other values exist, so a
published page path never changes when new data arrives.
"""

from __future__ import annotations

import hashlib
import re

from aicsbench.errors import BenchmarkError

_UNSAFE = re.compile(r"[^a-z0-9._-]")
_SUFFIXED = re.compile(r"-[0-9a-f]{8}$")


class SlugCollisionError(BenchmarkError):
    """Two different values would be written to the same page path."""


def slug(value: str) -> str:
    """The value itself when it is already a safe slug, else base slug plus a hash suffix."""
    base = _UNSAFE.sub("-", value.lower())
    if base and base == value and not base.startswith(".") and not _SUFFIXED.search(base):
        return base
    return f"{base}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:8]}"


class PathRegistry:
    """Hands out page paths and refuses one that another value already has."""

    def __init__(self) -> None:
        self._owners: dict[str, str] = {}

    def claim(self, path: str, owner: str) -> str:
        if path in self._owners:
            raise SlugCollisionError(
                f"page path {path} would be shared by {self._owners[path]} and {owner}"
            )
        self._owners[path] = owner
        return path
