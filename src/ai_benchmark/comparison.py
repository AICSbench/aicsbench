"""Aggregate comparable trial results for the CLI and future reports."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import mean, stdev

from ai_benchmark.models.enums import ComparisonDimension, TrialStatus
from ai_benchmark.models.results import TrialResult
from ai_benchmark.storage.base import ResultFilter

_HARNESS_VERSION_REFUSAL = (
    "harness version comparison mixes harnesses, models or stacks; filter to one of each"
)
SUITE_REFUSAL = "comparison mixes acceptance suites; pick one suite"

# The axes a row must hold fixed. A dimension that does not cover an axis leaves
# it to vary, so the selection must already have one value for it.
_COVERAGE: dict[ComparisonDimension, frozenset[str]] = {
    ComparisonDimension.AGENT: frozenset({"harness"}),
    ComparisonDimension.HARNESS: frozenset({"harness"}),
    ComparisonDimension.HARNESS_VERSION: frozenset({"harness"}),
    ComparisonDimension.MODEL: frozenset({"model"}),
    ComparisonDimension.AGENT_MODEL: frozenset({"harness", "model"}),
    ComparisonDimension.STACK: frozenset({"stack"}),
    ComparisonDimension.LANGUAGE: frozenset({"stack"}),
    ComparisonDimension.FRAMEWORK: frozenset({"stack"}),
    ComparisonDimension.TASK: frozenset(),
}

# New dimensions that are aliases for a combination. Expanding them makes
# ``--by harness_model`` produce exactly the rows of ``--by harness,model``.
_EXPANSIONS: dict[ComparisonDimension, tuple[ComparisonDimension, ...]] = {
    ComparisonDimension.HARNESS_MODEL: (
        ComparisonDimension.HARNESS,
        ComparisonDimension.MODEL,
    ),
}

_AXIS_PLURAL = {"harness": "harnesses", "model": "models", "stack": "stacks"}


@dataclass(frozen=True)
class ComparisonRow:
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


def _join_axes(axes: list[str], *, plural: bool) -> str:
    names = [_AXIS_PLURAL[axis] if plural else axis for axis in axes]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _normalize(
    dimension: ComparisonDimension | Iterable[ComparisonDimension],
) -> tuple[ComparisonDimension, ...]:
    """Validate the requested dimensions and expand combination aliases."""
    requested: tuple[ComparisonDimension, ...] = (
        (dimension,) if isinstance(dimension, ComparisonDimension) else tuple(dimension)
    )
    if not requested:
        raise ValueError("compare by at least one dimension")
    expanded: list[ComparisonDimension] = []
    for item in requested:
        if not isinstance(item, ComparisonDimension):
            raise ValueError(f"unknown comparison dimension: {item!r}")
        expanded.extend(_EXPANSIONS.get(item, (item,)))
    if len(expanded) > 3:
        raise ValueError("compare by at most 3 dimensions")
    for item in expanded:
        if expanded.count(item) > 1:
            raise ValueError(f"comparison dimension repeats: {item.value}")
    covered: dict[str, ComparisonDimension] = {}
    for item in expanded:
        for axis in _COVERAGE[item]:
            if axis in covered:
                raise ValueError(
                    f"dimensions {covered[axis].value} and {item.value} both group by "
                    f"the {axis} axis; pick one"
                )
            covered[axis] = item
    return tuple(expanded)


def _check_values(dimensions: tuple[ComparisonDimension, ...], trials: list[TrialResult]) -> None:
    harnesses = {trial.agent.name for trial in trials}
    models = {trial.agent.model_key for trial in trials}
    stacks = {trial.environment.stack for trial in trials}
    if dimensions == (ComparisonDimension.HARNESS_VERSION,) and (
        len(harnesses) > 1 or len(models) > 1 or len(stacks) > 1
    ):
        raise ValueError(_HARNESS_VERSION_REFUSAL)
    covered = {axis for dimension in dimensions for axis in _COVERAGE[dimension]}
    mixed = [
        axis
        for axis, values in (("harness", harnesses), ("model", models), ("stack", stacks))
        if axis not in covered and len(values) > 1
    ]
    if mixed:
        label = " + ".join(dimension.value for dimension in dimensions)
        raise ValueError(
            f"{label} comparison mixes {_join_axes(mixed, plural=True)}; "
            f"filter to one {_join_axes(mixed, plural=False)}"
        )


def compare_trials(
    trials: list[TrialResult],
    dimension: ComparisonDimension | Iterable[ComparisonDimension],
    min_trials: int = 3,
) -> list[ComparisonRow]:
    """Group trials by one dimension or by a combination of up to three.

    A row holds the harness (agent CLI), model key and stack fixed: each axis
    must be covered by a dimension or already have one value in the selection.
    """
    if min_trials < 1:
        raise ValueError("min_trials must be at least 1")
    keys = {trial.comparability_key() for trial in trials}
    if len(keys) > 1:
        raise ValueError(
            "results mix task/version, tooling mode, auth mode, repair budget, or "
            "parallelism; "
            "filter to one set"
        )
    dimensions = _normalize(dimension)
    _check_values(dimensions, trials)

    groups: dict[tuple[str, ...], list[TrialResult]] = {}
    for trial in trials:
        values = tuple(trial.dimension_value(item) for item in dimensions)
        groups.setdefault(values, []).append(trial)

    rows = []
    for values, group in groups.items():
        times = [
            trial.metrics.performance.agent_execution_seconds.value
            for trial in group
            if trial.status is TrialStatus.PASSED
            and not trial.rate_limited
            and trial.metrics.performance.agent_execution_seconds.is_available
            and trial.metrics.performance.agent_execution_seconds.value is not None
        ]
        costs = [
            trial.metrics.usage.cost.value
            for trial in group
            if trial.status is TrialStatus.PASSED
            and not trial.rate_limited
            and trial.metrics.usage.cost.is_available
            and trial.metrics.usage.cost.value is not None
        ]
        rows.append(
            ComparisonRow(
                value=" + ".join(values),
                trials=len(group),
                passed=sum(trial.status is TrialStatus.PASSED for trial in group),
                rate_limited=sum(trial.rate_limited for trial in group),
                timed=len(times),
                mean_seconds=mean(times) if times else None,
                stdev_seconds=stdev(times) if len(times) > 1 else None,
                mean_cost_usd=mean(costs) if costs else None,
                cost_samples=len(costs),
                low_confidence=len(group) < min_trials,
            )
        )
    return sorted(
        rows, key=lambda row: (row.mean_seconds is None, row.mean_seconds or 0, row.value)
    )


def version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    """Sort key for task versions: dot-separated parts, numbers before text."""
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in version.split("."))


@dataclass(frozen=True)
class MatrixColumn:
    """One task at one version: exactly ``compare_trials`` on that task's trials."""

    task: str
    task_version: str | None
    """None only when no trial matches the task (the column has no rows)."""
    rows: tuple[ComparisonRow, ...]
    refused: str | None


@dataclass(frozen=True)
class MatrixOverall:
    success_rate: float | None
    """Unweighted mean of the row's per-column success rates."""
    covered: int
    columns: int
    """Columns that count towards coverage: every column that is not refused."""
    mean_rank: float | None
    """Mean of the row's 1-based position in each covered column."""
    complete: bool


@dataclass(frozen=True)
class MatrixRow:
    value: str
    cells: tuple[ComparisonRow | None, ...]
    """One per column, in column order; None for no data or a refused column."""
    overall: MatrixOverall


@dataclass(frozen=True)
class ComparisonMatrix:
    columns: tuple[MatrixColumn, ...]
    rows: tuple[MatrixRow, ...]


def compare_matrix(
    trials: list[TrialResult],
    rows: ComparisonDimension | Iterable[ComparisonDimension],
    tasks: Sequence[str],
    filters: ResultFilter | None = None,
    min_trials: int = 3,
    *,
    suite_by_run: Mapping[str, str | None] | None = None,
) -> ComparisonMatrix:
    """Compare the same groups across several tasks (docs/phase-5/tasks/05-compare-page.md).

    ``tasks`` are task IDs, each optionally ``<id>@<version>``; without a version
    the newest version among the filtered trials is used. Each column is
    exactly ``compare_trials(by=rows)`` on that task version's filtered trials,
    so its refusals apply per column. ``suite_by_run`` maps run IDs to their
    acceptance suite hash; when given, a column mixing suites is refused too.

    Overall: the unweighted mean of the per-column success rates and the mean
    rank, over the columns that are not refused. Complete rows come first, by
    success rate (higher first), then mean rank, then value; rows missing a
    column follow, ordered the same way.
    """
    dimensions = tuple(rows) if not isinstance(rows, ComparisonDimension) else (rows,)
    selected = [trial for trial in trials if filters is None or filters.matches(trial)]
    columns: list[MatrixColumn] = []
    seen: set[tuple[str, str | None]] = set()
    for entry in tasks:
        task_id, _, pinned = entry.partition("@")
        of_task = [trial for trial in selected if trial.task_id == task_id]
        version = pinned or max(
            (trial.task_version for trial in of_task), key=version_key, default=None
        )
        if (task_id, version) in seen:
            continue  # Each task version counts once in the overall.
        seen.add((task_id, version))
        column_trials = [trial for trial in of_task if trial.task_version == version]
        refused: str | None = None
        column_rows: list[ComparisonRow] = []
        suites = {suite_by_run.get(t.run_id) for t in column_trials} if suite_by_run else set()
        if len(suites) > 1:
            refused = SUITE_REFUSAL
        else:
            try:
                column_rows = compare_trials(column_trials, dimensions, min_trials)
            except ValueError as exc:
                refused = str(exc)
        columns.append(MatrixColumn(task_id, version, tuple(column_rows), refused))

    counted = [column for column in columns if column.refused is None]
    values = list(dict.fromkeys(row.value for column in counted for row in column.rows))
    matrix_rows = []
    for value in values:
        cells = tuple(
            next((row for row in column.rows if row.value == value), None) for column in columns
        )
        rates = [cell.passed / cell.trials for cell in cells if cell is not None]
        ranks = [
            next(rank for rank, row in enumerate(column.rows, 1) if row.value == value)
            for column in counted
            if any(row.value == value for row in column.rows)
        ]
        overall = MatrixOverall(
            success_rate=mean(rates) if rates else None,
            covered=len(rates),
            columns=len(counted),
            mean_rank=mean(ranks) if ranks else None,
            complete=len(rates) == len(counted),
        )
        matrix_rows.append(MatrixRow(value, cells, overall))
    matrix_rows.sort(
        key=lambda row: (
            not row.overall.complete,
            -(row.overall.success_rate or 0.0),
            row.overall.mean_rank or 0.0,
            row.value,
        )
    )
    return ComparisonMatrix(tuple(columns), tuple(matrix_rows))
