#!/usr/bin/env python3
"""Refresh the bundled OpenRouter model price snapshot.

The snapshot keeps every catalog model that accepts and returns text, with
only the base prompt, completion and cache-read token rates. OpenRouter's
route/time/context overrides and non-token fees are intentionally omitted.

Usage:
    python scripts/refresh_openrouter_models.py
    python scripts/refresh_openrouter_models.py --input models.json
    python scripts/refresh_openrouter_models.py --retrieved-at 2026-11-21T11:37:56Z

``--input`` regenerates from an already-downloaded API response, so the
snapshot can be refreshed offline and the transformation tested without the
network.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_benchmark.pricing import SOURCE  # noqa: E402

KEPT_RATES = ("prompt", "completion", "input_cache_read")
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / "src" / "ai_benchmark" / "pricing"


def fetch_payload() -> dict[str, Any]:
    request = urllib.request.Request(SOURCE, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("OpenRouter returned a non-object response")
    return payload


def build_snapshot(payload: dict[str, Any], retrieved_at: datetime) -> dict[str, Any]:
    entries = payload.get("data")
    if not isinstance(entries, list):
        raise ValueError("OpenRouter response needs a data list")
    total = payload.get("total_count")
    if isinstance(total, int) and total != len(entries):
        raise ValueError("OpenRouter response is truncated: total_count does not match data")
    models: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        architecture = entry.get("architecture")
        pricing = entry.get("pricing")
        model_id = entry.get("id")
        if (
            not isinstance(architecture, dict)
            or not isinstance(pricing, dict)
            or not isinstance(model_id, str)
            or not model_id
        ):
            continue
        if "text" not in (architecture.get("input_modalities") or []):
            continue
        if "text" not in (architecture.get("output_modalities") or []):
            continue
        models.append(
            {
                "id": model_id,
                "name": entry.get("name") or model_id,
                "context_length": entry.get("context_length"),
                "pricing": {key: pricing[key] for key in KEPT_RATES if key in pricing},
            }
        )
    if not models:
        raise ValueError("OpenRouter response had no text models")
    models.sort(key=lambda model: model["id"])
    retrieved = retrieved_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "source": SOURCE,
        "retrieved_at": retrieved,
        "filter": "models with text input and text output",
        "model_count": len(models),
        "models": models,
    }


def parse_retrieved_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Read a saved API response instead of fetching.")
    parser.add_argument("--output", type=Path, help="Write here instead of the dated default path.")
    parser.add_argument(
        "--retrieved-at",
        help="ISO-8601 snapshot time (default: now, UTC).",
    )
    args = parser.parse_args()

    retrieved_at = (
        parse_retrieved_at(args.retrieved_at) if args.retrieved_at else datetime.now(timezone.utc)
    )
    payload = json.loads(args.input.read_text(encoding="utf-8")) if args.input else fetch_payload()
    snapshot = build_snapshot(payload, retrieved_at)
    output = (
        args.output or DEFAULT_DIRECTORY / f"openrouter-models-{snapshot['retrieved_at'][:10]}.json"
    )
    output.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output} ({snapshot['model_count']} models, {snapshot['retrieved_at']})")


if __name__ == "__main__":
    main()
