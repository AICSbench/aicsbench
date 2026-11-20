"""Pydantic models the site export is written through (docs/phase-5/schemas.md#site-export).

``schema/*.schema.json`` in every export is generated from these models, so
the files and their JSON Schemas cannot drift apart. ``aicsbench/web``
generates its types from those schemas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter

EXPORT_SCHEMA = 1
"""Bump when a key is removed or renamed; adding a key keeps it."""

PageKind = Literal[
    "home", "task", "harness", "model", "stack", "leaderboard", "run", "compare", "methodology"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportInfo(_Strict):
    """``export.json``."""

    export_schema: Literal[1]
    built_at: datetime
    platform_version: str
    platform_git_commit: str | None
    data_commit: str | None
    base_url: str


class TrialRow(_Strict):
    """One object of ``data/trials.json``: ``reporting.trial_row`` plus export-only keys."""

    run_id: str
    trial_id: str
    trial_index: int
    max_repairs: int
    max_parallel: int
    task: str
    task_version: str
    agent: str
    model: str
    stack: str
    language: str
    language_version: str
    framework: str
    framework_version: str
    tooling_mode: str
    auth_mode: str
    status: str
    rate_limited: bool
    agent_seconds: float | None
    wall_seconds: float | None
    tests_passed: int | None
    tests_failed: int | None
    tests_total: int | None
    repair_iterations: int | None
    token_scheme: str | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None
    cost_availability: str
    harness: str
    harness_version: str | None
    requested_model: str | None
    reasoning_effort: str | None
    language_version_installed: str | None
    framework_version_installed: str | None
    # Export-only keys.
    verified: bool
    logs_available: bool
    suite_sha256: str
    submitted_at: datetime
    submitter: str


class MetaTask(_Strict):
    id: str
    version: str
    name: str | None
    """None when the task is not in the catalog the export ran with."""
    description: str | None


class Meta(_Strict):
    """``data/meta.json``."""

    runs: int
    trials: int
    tasks: list[MetaTask]


class Row(_Strict):
    """``dataclasses.asdict(comparison.ComparisonRow)`` plus ``success_rate``."""

    value: str
    trials: int
    passed: int
    rate_limited: int
    timed: int
    mean_seconds: float | None
    stdev_seconds: float | None
    mean_cost_usd: float | None
    cost_samples: int
    low_confidence: bool
    success_rate: float


class MatrixColumn(_Strict):
    task: str
    task_version: str | None
    path: str
    """The task page."""
    refused: str | None


class MatrixOverall(_Strict):
    success_rate: float | None
    covered: int
    columns: int
    mean_rank: float | None
    complete: bool


class MatrixRow(_Strict):
    value: str
    cells: list[Row | None]
    """One per column, in column order; null for no data or a refused column."""
    overall: MatrixOverall


class Matrix(_Strict):
    """``comparison.compare_matrix``: used by the ``/leaderboard/`` page."""

    columns: list[MatrixColumn]
    rows: list[MatrixRow]


class Section(_Strict):
    heading: str
    fixed: dict[str, str]
    by: str
    verified_only: bool
    """True: verified runs only. False: community (unverified) runs only."""
    rows: list[Row]
    refused: str | None
    matrix: Matrix | None = None


class Link(_Strict):
    path: str
    label: str


class Page(_Strict):
    path: str
    kind: PageKind
    title: str
    description: str
    params: dict[str, str]
    sections: list[Section]
    links: list[Link]


class Pages(_Strict):
    """``pages.json``."""

    pages: list[Page]


TRIALS = TypeAdapter(list[TrialRow])

SCHEMAS: dict[str, TypeAdapter[Any]] = {
    "export": TypeAdapter(ExportInfo),
    "trials": TRIALS,
    "meta": TypeAdapter(Meta),
    "pages": TypeAdapter(Pages),
}
"""Name in ``schema/<name>.schema.json`` -> the model that file describes."""
