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
    """JSON paths (of keys and values) in ``value`` that look like secrets."""
    matches: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{_prefix}.{key}" if _prefix else str(key)
            if _matches_patterns(str(key)) or (
                str(key).lower() in SECRET_KEYS and isinstance(item, str) and item
            ):
                matches.append(path)
            matches.extend(find_secrets(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            matches.extend(find_secrets(item, f"{_prefix}[{index}]"))
    elif isinstance(value, str) and _matches_patterns(value):
        matches.append(_prefix or "<value>")
    return matches


def scan_text(text: str) -> list[str]:
    """Locations in a log file's text that look like secrets.

    Each line is checked against :data:`SECRET_PATTERNS` and the structured
    quoted-value pattern; every line that parses as JSON is additionally
    checked with :func:`find_secrets`, so ``SECRET_KEYS`` apply to JSONL logs.
    """
    matches: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        where = f"line {number}"
        if any(pattern.search(line) for pattern in SECRET_PATTERNS) or _STRUCTURED_SECRET.search(
            line
        ):
            matches.append(where)
            continue
        stripped = line.strip()
        if not stripped.startswith(("{", "[")):
            continue
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        matches.extend(
            f"{where} {path}" if path != "<value>" else where for path in find_secrets(data)
        )
    return matches


def _matches_patterns(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)
