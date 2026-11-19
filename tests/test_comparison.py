from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_benchmark.cli.app import app
from ai_benchmark.comparison import compare_trials
from ai_benchmark.models import (
    ComparisonDimension,
    Metric,
    MetricSource,
    TrialResult,
    TrialStatus,
)
from ai_benchmark.storage import JsonResultStore

from .test_storage import manifest, trial


def _modeled(
    trial_id: str,
    *,
    agent: str = "codex",
    model: str | None = None,
    stack: str = "fastapi",
    language: str = "python",
    version: str | None = None,
) -> TrialResult:
    item = trial("r1", trial_id, agent, TrialStatus.PASSED)
    item.agent.resolved_model = model
    item.agent.version = version
    item.environment.stack = stack
    item.environment.language = language
    return item


def test_compare_ranks_passing_times_and_counts_limit_hits() -> None:
    fast = [trial("r1", f"f{i}", "codex", TrialStatus.PASSED) for i in range(3)]
    for item, seconds in zip(fast, [10.0, 12.0, 14.0], strict=True):
        item.metrics.performance.agent_execution_seconds = Metric[float].of(
            seconds, MetricSource.MEASURED
        )
        item.metrics.usage.cost = Metric[float].of(0.02, MetricSource.AGENT_REPORTED)
    slow = trial("r1", "s1", "codex", TrialStatus.PASSED, limited=True)
    slow.environment.stack = "laravel"
    slow.metrics.performance.agent_execution_seconds = Metric[float].of(1.0, MetricSource.MEASURED)
    failed = trial("r1", "s2", "codex", TrialStatus.FAILED)
    failed.environment.stack = "laravel"

    rows = compare_trials([*fast, slow, failed], ComparisonDimension.STACK)
    assert [row.value for row in rows] == ["fastapi", "laravel"]
    assert (rows[0].passed, rows[0].trials, rows[0].mean_seconds, rows[0].stdev_seconds) == (
        3, 3, 12.0, 2.0
    )
    assert (rows[1].passed, rows[1].rate_limited, rows[1].timed, rows[1].mean_seconds) == (
        1, 1, 0, None
    )
    assert rows[1].low_confidence and not rows[0].low_confidence
    assert rows[0].mean_cost_usd == 0.02 and rows[1].mean_cost_usd is None
    assert (rows[0].cost_samples, rows[1].cost_samples) == (3, 0)


def test_compare_rejects_mixed_task_versions() -> None:
    a = trial("r1", "a", "codex", TrialStatus.PASSED)
    b = trial("r1", "b", "codex", TrialStatus.PASSED)
    b.task_version = "2"
    with pytest.raises(ValueError, match="task/version"):
        compare_trials([a, b], ComparisonDimension.STACK)


@pytest.mark.parametrize("setting", ["max_repairs", "max_parallel"])
def test_compare_rejects_mixed_execution_settings(setting: str) -> None:
    a = trial("r1", "a", "codex", TrialStatus.PASSED)
    b = trial("r1", "b", "codex", TrialStatus.PASSED)
    setattr(b, setting, 2)
    with pytest.raises(ValueError, match="repair budget|parallelism"):
        compare_trials([a, b], ComparisonDimension.STACK)


def test_compare_rejects_mixed_model_in_stack_view() -> None:
    a = trial("r1", "a", "codex", TrialStatus.PASSED)
    b = trial("r1", "b", "codex", TrialStatus.PASSED)
    b.environment.stack = "laravel"
    b.agent.resolved_model = "other-model"
    with pytest.raises(ValueError, match="mixes models"):
        compare_trials([a, b], ComparisonDimension.STACK)


def test_compare_cli_reads_saved_results(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    item = trial("r1", "t1", "codex", TrialStatus.PASSED)
    item.metrics.performance.agent_execution_seconds = Metric[float].of(9.0, MetricSource.MEASURED)
    store.save_trial(item)
    result = CliRunner().invoke(app, ["--results-dir", str(tmp_path), "compare", "--run", "r1"])
    assert result.exit_code == 0, result.output
    assert "fastapi" in result.output and "1/1" in result.output and "9.0s" in result.output


def test_harness_dimensions_group_and_report_versions() -> None:
    trials = [
        _modeled("a", model="gpt-6-luna", version="0.167.2"),
        _modeled("b", model="gpt-6-luna", version="0.167.1"),
        _modeled("c", model="gpt-6-luna", version=None),
    ]

    assert [row.value for row in compare_trials(trials, ComparisonDimension.HARNESS)] == ["codex"]
    versions = {row.value for row in compare_trials(trials, ComparisonDimension.HARNESS_VERSION)}
    assert versions == {"codex 0.167.2", "codex 0.167.1", "codex unknown"}


def test_harness_version_refuses_mixed_harness_model_or_stack() -> None:
    base = _modeled("a", model="gpt-6-luna", version="0.167.2")
    mixed = [
        _modeled("b", agent="opencode", model="openai/gpt-6-luna", version="1.19.5"),
        _modeled("c", model="gpt-6", version="0.167.2"),
        _modeled("d", model="gpt-6-luna", stack="laravel", version="0.167.2"),
    ]
    for other in mixed:
        with pytest.raises(
            ValueError,
            match="harness version comparison mixes harnesses, models or stacks",
        ):
            compare_trials([base, other], ComparisonDimension.HARNESS_VERSION)


def test_harness_version_combined_with_model_groups_versions_per_model() -> None:
    trials = [
        _modeled("a", model="m1", version="1"),
        _modeled("b", model="m1", version="2"),
        _modeled("c", model="m2", version="1"),
        _modeled("d", model="m2", version="2"),
    ]
    rows = compare_trials(trials, [ComparisonDimension.HARNESS_VERSION, ComparisonDimension.MODEL])
    assert {row.value for row in rows} == {
        "codex 1 + m1",
        "codex 2 + m1",
        "codex 1 + m2",
        "codex 2 + m2",
    }


def test_same_model_key_compares_across_harnesses() -> None:
    codex = _modeled("a", agent="codex", model="gpt-6-luna")
    opencode = _modeled("b", agent="opencode", model="openai/gpt-6-luna")

    for dimension in (ComparisonDimension.HARNESS, ComparisonDimension.AGENT):
        rows = compare_trials([codex, opencode], dimension)
        assert {row.value for row in rows} == {"codex", "opencode"}

    other_model = _modeled("c", agent="opencode", model="openai/gpt-5")
    with pytest.raises(ValueError, match="mixes models"):
        compare_trials([codex, other_model], ComparisonDimension.HARNESS)


def test_combined_dimensions_make_one_row_per_combination() -> None:
    trials = [
        _modeled("a", model="m1", stack="fastapi"),
        _modeled("b", model="m2", stack="fastapi"),
        _modeled("c", model="m1", stack="laravel"),
        _modeled("d", model="m2", stack="laravel"),
    ]

    rows = compare_trials(trials, [ComparisonDimension.STACK, ComparisonDimension.MODEL])
    assert {row.value for row in rows} == {
        "fastapi + m1",
        "fastapi + m2",
        "laravel + m1",
        "laravel + m2",
    }
    assert all(row.trials == 1 for row in rows)


def test_combined_dimensions_refuse_an_uncovered_axis() -> None:
    one = _modeled("a", agent="codex", model="gpt-6-luna", stack="fastapi")
    two = _modeled("b", agent="opencode", model="openai/gpt-6-luna", stack="laravel")
    with pytest.raises(ValueError, match="mixes harnesses; filter to one harness"):
        compare_trials([one, two], [ComparisonDimension.STACK, ComparisonDimension.MODEL])


def test_language_dimension_pools_stacks() -> None:
    trials = [
        _modeled("a", model="m1", stack="laravel", language="php"),
        _modeled("b", model="m1", stack="symfony", language="php"),
    ]
    rows = compare_trials(trials, [ComparisonDimension.LANGUAGE, ComparisonDimension.MODEL])
    assert [(row.value, row.trials) for row in rows] == [("php + m1", 2)]


def test_harness_model_groups_like_harness_and_model() -> None:
    trials = [
        _modeled("a", model="gpt-6-luna"),
        _modeled("b", model="gpt-6-luna"),
        _modeled("c", model="gpt-6"),
    ]

    alias = compare_trials(trials, ComparisonDimension.HARNESS_MODEL)
    combined = compare_trials(trials, [ComparisonDimension.HARNESS, ComparisonDimension.MODEL])
    assert [(row.value, row.trials, row.passed) for row in alias] == [
        (row.value, row.trials, row.passed) for row in combined
    ]


def test_combined_dimensions_validate_count_duplicates_and_overlap() -> None:
    item = _modeled("a", model="m1")
    with pytest.raises(ValueError, match="at most 3 dimensions"):
        compare_trials(
            [item],
            [
                ComparisonDimension.STACK,
                ComparisonDimension.MODEL,
                ComparisonDimension.TASK,
                ComparisonDimension.LANGUAGE,
            ],
        )
    with pytest.raises(ValueError, match="repeats: stack"):
        compare_trials([item], [ComparisonDimension.STACK, ComparisonDimension.STACK])
    with pytest.raises(ValueError, match="both group by the stack axis"):
        compare_trials([item], [ComparisonDimension.STACK, ComparisonDimension.LANGUAGE])


def test_compare_cli_accepts_combined_dimensions(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    for index, (stack, model) in enumerate(
        [("fastapi", "m1"), ("fastapi", "m2"), ("laravel", "m1"), ("laravel", "m2")]
    ):
        store.save_trial(_modeled(f"t{index}", model=model, stack=stack))

    result = CliRunner().invoke(
        app,
        ["--results-dir", str(tmp_path), "compare", "--run", "r1", "--by", "stack,model"],
    )
    assert result.exit_code == 0, result.output
    assert "stack + model" in result.output
    assert "fastapi + m1" in result.output


def test_compare_cli_harness_version_and_unknown_dimension(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    store.save_trial(_modeled("a", model="gpt-6-luna", version="0.167.2"))
    store.save_trial(_modeled("b", model="gpt-6-luna", version=None))
    runner = CliRunner()
    args = ["--results-dir", str(tmp_path), "compare", "--run", "r1"]

    result = runner.invoke(app, [*args, "--by", "harness_version"])
    assert result.exit_code == 0, result.output
    assert "codex 0.167.2" in result.output and "codex unknown" in result.output

    bad = runner.invoke(app, [*args, "--by", "nope"])
    assert bad.exit_code == 1
    assert "unknown --by value 'nope'" in bad.output
