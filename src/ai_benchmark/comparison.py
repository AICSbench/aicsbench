"""Aggregate comparable trial results for the CLI and future reports."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import mean, stdev

from ai_benchmark.models.enums import ComparisonDimension, TrialStatus
from ai_benchmark.models.results import TrialResult

_HARNESS_VERSION_REFUSAL = (
    "harness version comparison mixes harnesses, models or stacks; filter to one of each"
)
_PROVIDER_REFUSAL = "harness comparison mixes providers; filter to one provider"
_UNKNOWN_PROVIDER_REFUSAL = (
    "harness comparison needs a recorded provider; exclude trials recorded before provider tracking"
)

# The harness dimensions hold every axis fixed, including the provider (task 9).
_HARNESS_DIMENSIONS = frozenset(
    {
        ComparisonDimension.AGENT,
        ComparisonDimension.HARNESS,
        ComparisonDimension.HARNESS_VERSION,
    }
)

# The axes a row must hold fixed. A dimension that does not cover an axis leaves
# it to vary, so the selection must already have one value for it.
_COVERAGE: dict[ComparisonDimension, frozenset[str]] = {
    ComparisonDimension.AGENT: frozenset({"harness"}),
    ComparisonDimension.HARNESS: frozenset({"harness"}),
    ComparisonDimension.HARNESS_VERSION: frozenset({"harness"}),
    ComparisonDimension.MODEL: frozenset({"model"}),
    ComparisonDimension.AGENT_MODEL: frozenset({"harness", "model"}),
    ComparisonDimension.PROVIDER: frozenset(),
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
    providers = {trial.agent.provider for trial in trials}
    if dimensions in ((ComparisonDimension.HARNESS_VERSION,), (ComparisonDimension.PROVIDER,)) and (
        len(harnesses) > 1 or len(models) > 1 or len(stacks) > 1
    ):
        raise ValueError(_HARNESS_VERSION_REFUSAL)
    if _HARNESS_DIMENSIONS.intersection(dimensions):
        # A harness run's provider must be recorded and single: a Codex run
        # against OpenAI directly is never ranked against a Claude Code run
        # through OpenRouter, and two unknown-provider trials may have used
        # different gateways.
        if None in providers:
            raise ValueError(_UNKNOWN_PROVIDER_REFUSAL)
        if len(providers) > 1:
            raise ValueError(_PROVIDER_REFUSAL)
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
