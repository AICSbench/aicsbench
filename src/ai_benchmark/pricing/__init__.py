"""Bundled OpenRouter catalog and base token prices.

The catalog is a dated snapshot of ``https://openrouter.ai/api/v1/models`` so
``report`` and ``export`` work without the network. Refresh it with
``python scripts/refresh_openrouter_models.py``.

Only OpenRouter's listed base prompt, completion and cache-read token rates are
used. They are a fixed comparison baseline, not an official vendor price or an
actual bill: route, time-of-day and context-length overrides, cache-write and
separate internal-reasoning rates, and non-token fees are not applied.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

SOURCE = "https://openrouter.ai/api/v1/models"
"""The public API the bundled snapshot was taken from."""

SNAPSHOT_PREFIX = "openrouter-models-"
"""Filename prefix for dated snapshots shipped in this package."""


def snapshot_filename(retrieved_at: str) -> str:
    return f"{SNAPSHOT_PREFIX}{retrieved_at.replace(':', '-')}.json"


def _bundled_snapshot() -> Any:
    """The newest dated snapshot in the package."""
    candidates = sorted(
        (
            entry
            for entry in files(__package__).iterdir()
            if entry.name.startswith(SNAPSHOT_PREFIX) and entry.name.endswith(".json")
        ),
        key=lambda entry: entry.name,
    )
    if not candidates:
        raise FileNotFoundError(f"no {SNAPSHOT_PREFIX}*.json snapshot is bundled")
    return candidates[-1]


@dataclass(frozen=True)
class ModelPrice:
    """One catalog entry's base token rates, in USD per token."""

    model_id: str
    prompt: float | None
    completion: float | None
    cache_read: float | None
    retrieved_at: str
    source: str = SOURCE

    @property
    def label(self) -> str:
        """A stable, explicit rate-card label for reports and exports."""
        return f"openrouter/{self.model_id}/base@{self.retrieved_at}"

    @property
    def is_priceable(self) -> bool:
        """True when the entry has the base input and output rates we require."""
        return self.prompt is not None and self.completion is not None


@dataclass(frozen=True)
class Catalog:
    """A dated snapshot indexed by exact lower-case model ID."""

    retrieved_at: str
    models: Mapping[str, ModelPrice]
    source: str = SOURCE

    def lookup(self, model: str | None) -> ModelPrice | None:
        """Find an exact ID, or the only entry with that bare ID.

        A bare ID that matches more than one entry is ambiguous and returns
        ``None``. Provider prefixes are never stripped or guessed.
        """
        if not model:
            return None
        key = model.strip().lower()
        if not key:
            return None
        if "/" in key:
            return self.models.get(key)
        matches = [
            price
            for price in self.models.values()
            if price.model_id.rsplit("/", 1)[-1].lower() == key
        ]
        return matches[0] if len(matches) == 1 else None


def _rate(value: Any) -> float | None:
    """A finite, non-negative per-token rate, or None when unusable."""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        rate = float(value)
    except ValueError:
        return None
    return rate if math.isfinite(rate) and rate >= 0 else None


def load_catalog(path: str | Path | None = None) -> Catalog:
    """Load the bundled snapshot, or ``path`` when given (tests/refresh)."""
    if path is None:
        text = _bundled_snapshot().read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    document = json.loads(text)
    if not isinstance(document, dict):
        raise ValueError("OpenRouter snapshot must be a JSON object")
    source = document.get("source")
    if source != SOURCE:
        raise ValueError(f"unexpected OpenRouter snapshot source: {source!r}")
    retrieved_at = document.get("retrieved_at")
    if not isinstance(retrieved_at, str):
        raise ValueError("OpenRouter snapshot needs a retrieved_at timestamp")
    timestamp = datetime.strptime(retrieved_at, "%Y-%m-%dT%H:%M:%SZ")
    if timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") != retrieved_at:
        raise ValueError(f"invalid OpenRouter snapshot timestamp: {retrieved_at!r}")
    entries = document.get("models")
    if not isinstance(entries, list):
        raise ValueError("OpenRouter snapshot needs a models list")
    if not entries:
        raise ValueError("OpenRouter snapshot has no models")
    declared = document.get("model_count")
    if declared is not None and declared != len(entries):
        raise ValueError("OpenRouter snapshot model_count does not match its models")
    models: dict[str, ModelPrice] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("OpenRouter snapshot models must be objects")
        model_id = entry.get("id")
        pricing = entry.get("pricing")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("OpenRouter snapshot entry needs an id")
        if not isinstance(pricing, dict):
            raise ValueError(f"OpenRouter snapshot entry {model_id!r} needs pricing")
        key = model_id.lower()
        if key in models:
            raise ValueError(f"duplicate OpenRouter model id: {model_id!r}")
        models[key] = ModelPrice(
            model_id=model_id,
            prompt=_rate(pricing.get("prompt")),
            completion=_rate(pricing.get("completion")),
            cache_read=_rate(pricing.get("input_cache_read")),
            retrieved_at=retrieved_at,
            source=source,
        )
    return Catalog(retrieved_at=retrieved_at, models=models, source=source)


@lru_cache(maxsize=1)
def default_catalog() -> Catalog:
    """The snapshot bundled with the package."""
    return load_catalog()
