"""Secret scanning for submission bundles (docs/phase-5/schemas.md#sanitizing).

A safety net, not a guarantee: ``submit`` scans everything it writes and the
validator scans every bundle again. Nothing here reads the network.
"""

from __future__ import annotations

import json
import re
from typing import Any

SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(pattern)
    for pattern in (
        r"sk-[A-Za-z0-9_-]{20,}",
        r"sk-ant-[A-Za-z0-9_-]{20,}",
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        r"xox[baprs]-[A-Za-z0-9-]{10,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.",
        r"(?i)(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|OPENAI_API_KEY|CODEX_API_KEY)=\S+",
        r"(A3T[A-Z0-9]|AKIA|ASIA)[A-Z0-9]{16}",
        r"\b[A-Z0-9_]*(SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|ACCESS_KEY)[A-Z0-9_]*"
        r'=["\']?(?=[^\s"\']*[A-Za-z])[^\s"\']{8,}',
    )
]
"""Key-like patterns flagged in string values and JSON keys alike."""

SECRET_KEYS: frozenset[str] = frozenset(
    {
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "apikey",
        "authorization",
        "password",
        "client_secret",
        "secret",
    }
)
"""JSON keys whose non-empty string values are always flagged."""

_STRUCTURED_SECRET = re.compile(
    r'(?i)["\']?(access_token|refresh_token|id_token|api_key|apikey|authorization'
    r'|password|client_secret|secret)["\']?\s*[:=]\s*["\'][^"\']{4,}["\']'
)
"""Quoted ``key: "value"`` assignments in plain-text logs (``"secret": false`` is fine)."""


def find_secrets(value: Any, _prefix: str = "") -> list[str]:
    """JSON paths (of keys and values) in ``value`` that look like secrets.

    Walks iteratively so deeply nested JSON cannot hit the recursion limit.
    A key that is itself a secret is redacted in the returned path so the
    credential never leaks into reports; an ordinary secret-named key is
    kept (``raw.access_token``). Values never appear in the paths.
    """
    matches: list[str] = []
    stack: list[tuple[Any, str, str | None]] = [(value, _prefix, None)]
    while stack:
        node, prefix, key = stack.pop()
        if key is not None and (
            _matches_patterns(str(key))
            or (str(key).lower() in SECRET_KEYS and isinstance(node, str) and node)
        ):
            matches.append(prefix)
        if isinstance(node, dict):
            for child_key, item in reversed(list(node.items())):
                segment = _path_segment(str(child_key))
                path = f"{prefix}.{segment}" if prefix else segment
                stack.append((item, path, child_key))
        elif isinstance(node, list):
            for index, item in reversed(list(enumerate(node))):
                stack.append((item, f"{prefix}[{index}]", None))
        elif isinstance(node, str) and _matches_patterns(node):
            matches.append(prefix or "<value>")
    return matches


def _path_segment(key: str) -> str:
    """The path segment for a JSON key; a secret-bearing key is redacted."""
    return "<secret-key>" if _matches_patterns(key) else key


def scan_text(text: str) -> list[str]:
    """Locations in a log file's text that look like secrets.

    Structured assignments may span lines, so the whole text is checked with
    the structured pattern first and the line of each match is reported.
    Each line is then checked against :data:`SECRET_PATTERNS`, and every line
    that parses as JSON (JSONL logs) is additionally checked with
    :func:`find_secrets`, so ``SECRET_KEYS`` apply there too.
    """
    matches: list[str] = []
    # finditer yields matches in order, so newline counts accumulate across
    # the spans between matches instead of rescanning from position 0.
    structured_lines: set[int] = set()
    previous = 0
    newlines = 0
    for match in _STRUCTURED_SECRET.finditer(text):
        newlines += text.count("\n", previous, match.start())
        previous = match.start()
        structured_lines.add(newlines + 1)
    for number, line in enumerate(text.splitlines(), 1):
        where = f"line {number}"
        if number in structured_lines or any(pattern.search(line) for pattern in SECRET_PATTERNS):
            matches.append(where)
            continue
        stripped = line.strip()
        if not stripped.startswith(("{", "[")):
            continue
        try:
            data = json.loads(stripped)
        except (ValueError, RecursionError):
            # ValueError covers JSONDecodeError and the plain ValueError
            # Python 3.11+ raises for very long integers.
            continue
        matches.extend(
            f"{where} {path}" if path != "<value>" else where for path in find_secrets(data)
        )
    return matches


def _matches_patterns(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)
