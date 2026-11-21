import csv
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_benchmark.cli.app import app
from ai_benchmark.comparison import compare_trials
from ai_benchmark.models import (
    AuthMode,
    ComparisonDimension,
    Metric,
    MetricSource,
    ToolingMode,
    TrialStatus,
)
from ai_benchmark.reporting import csv_text, export_trials, render_report
from ai_benchmark.storage import JsonResultStore, ResultFilter

from .test_storage import manifest, trial


def test_flat_export_keeps_unavailable_metrics_null() -> None:
    item = trial("r1", "t1", "codex", TrialStatus.PASSED)
    item.agent.version = "0.167.2"
    item.agent.requested_model = "gpt-6-luna"
    item.agent.reasoning_effort = "high"
    item.metrics.performance.agent_execution_seconds = Metric[float].of(7.5, MetricSource.MEASURED)
    row = json.loads(export_trials([item], "jsonl"))
    assert row["agent_seconds"] == 7.5
    assert row["cost_usd"] is None and row["cost_availability"] == "unavailable"
    assert row["input_tokens"] is None
    assert (row["harness"], row["harness_version"]) == ("codex", "0.167.2")
    assert (row["requested_model"], row["reasoning_effort"]) == ("gpt-6-luna", "high")
    assert list(row)[-13:-4] == [
        "harness",
        "harness_version",
        "requested_model",
        "reasoning_effort",
        "language_version_installed",
        "framework_version_installed",
        "provider",
        "endpoint_host",
        "framework_version_changed",
    ]
    csv_row = next(csv.DictReader(io.StringIO(export_trials([item], "csv"))))
    assert csv_row["agent_seconds"] == "7.5" and csv_row["cost_usd"] == ""
    assert csv_row["harness_version"] == "0.167.2"
    assert csv_row["reasoning_effort"] == "high"


def test_export_reads_trials_saved_before_harness_columns(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    payload = trial("r1", "t1", "codex", TrialStatus.PASSED).model_dump(mode="json")
    for key in ("version", "requested_model", "reasoning_effort"):
        payload["agent"].pop(key)
    path = tmp_path / "r1" / "trials" / "t1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    trials = list(store.iter_trials(ResultFilter(run_id="r1")))
    row = next(csv.DictReader(io.StringIO(export_trials(trials, "csv"))))
    assert row["harness"] == "codex"
    assert row["harness_version"] == ""
    assert row["requested_model"] == ""
    assert row["reasoning_effort"] == ""


def test_export_adds_installed_version_columns() -> None:
    item = trial("r1", "t1", "codex", TrialStatus.PASSED)
    item.environment.installed_versions = {"language": "3.12.7", "framework": "0.115.2"}
    row = json.loads(export_trials([item], "jsonl"))
    assert row["language_version_installed"] == "3.12.7"
    assert row["framework_version_installed"] == "0.115.2"
    assert list(row)[-9:-4] == [
        "language_version_installed",
        "framework_version_installed",
        "provider",
        "endpoint_host",
        "framework_version_changed",
    ]


def test_export_reads_trials_saved_before_installed_versions(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    payload = trial("r1", "t1", "codex", TrialStatus.PASSED).model_dump(mode="json")
    payload["environment"].pop("installed_versions")
    path = tmp_path / "r1" / "trials" / "t1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    trials = list(store.iter_trials(ResultFilter(run_id="r1")))
    row = next(csv.DictReader(io.StringIO(export_trials(trials, "csv"))))
    assert row["language_version"] == "3.12"  # the declared version is unchanged
    assert row["language_version_installed"] == "" and row["framework_version_installed"] == ""
    # Recorded before version selection: the mismatch was never checked.
    assert row["framework_version_changed"] == ""


def test_comparability_ignores_installed_versions() -> None:
    a = trial("r1", "a", "codex", TrialStatus.PASSED)
    b = trial("r1", "b", "codex", TrialStatus.PASSED)
    b.environment.installed_versions = {"language": "3.13.1"}
    assert a.comparability_key() == b.comparability_key()
    assert len(compare_trials([a, b], ComparisonDimension.STACK)) == 1


def test_report_and_export_cli(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    item = trial("r1", "t1", "codex", TrialStatus.PASSED)
    item.metrics.performance.agent_execution_seconds = Metric[float].of(7.5, MetricSource.MEASURED)
    store.save_trial(item)
    runner = CliRunner()
    args = ["--results-dir", str(tmp_path)]

    exported = runner.invoke(app, [*args, "export", "--run", "r1", "--format", "jsonl"])
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.output)["trial_id"] == "t1"

    report = runner.invoke(app, [*args, "report", "--run", "r1"])
    assert report.exit_code == 0, report.output
    assert "| fastapi | 1/1 |" in report.output
    assert "100%" in report.output
    report_json = runner.invoke(app, [*args, "report", "--run", "r1", "--format", "json"])
    assert json.loads(report_json.output)["groups"][0]["mean_seconds"] == 7.5
    report_csv = runner.invoke(app, [*args, "report", "--run", "r1", "--format", "csv"])
    assert next(csv.DictReader(io.StringIO(report_csv.output)))["value"] == "fastapi"


def test_cli_estimates_luna_cost_with_partial_coverage(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    item = trial("r1", "priced", "codex", TrialStatus.FAILED)
    item.agent.provider = "openai"
    item.agent.requested_model = item.agent.resolved_model = "gpt-6-luna"
    tokens = item.metrics.usage.tokens
    tokens.accounting_scheme = "openai"
    source = MetricSource.AGENT_REPORTED
    tokens.input_tokens = Metric[int].of(100_000, source)
    tokens.cached_input_tokens = Metric[int].of(80_000, source)
    tokens.output_tokens = Metric[int].of(4_000, source)
    tokens.reasoning_tokens = Metric[int].of(2_000, source)
    store.save_trial(item)
    for trial_id in ("no-cache-count", "custom-endpoint", "legacy-provider"):
        other = item.model_copy(deep=True, update={"trial_id": trial_id})
        if trial_id == "no-cache-count":
            other.metrics.usage.tokens.cached_input_tokens = Metric[int].unavailable()
        elif trial_id == "custom-endpoint":
            other.agent.endpoint_host = "gateway.example"
        else:
            other.agent.provider = None
        store.save_trial(other)

    runner = CliRunner()
    args = ["--results-dir", str(tmp_path)]
    exported = runner.invoke(app, [*args, "export", "--run", "r1", "--format", "jsonl"])
    assert exported.exit_code == 0, exported.output
    rows = {row["trial_id"]: row for row in map(json.loads, exported.output.splitlines())}
    assert rows["priced"]["estimated_api_cost_usd"] == pytest.approx(0.0048)
    assert rows["priced"]["estimated_api_cost_model_assumed"] is True
    assert rows["priced"]["cost_usd"] is None
    assert rows["no-cache-count"]["estimated_api_cost_usd"] is None
    assert rows["custom-endpoint"]["estimated_api_cost_usd"] == pytest.approx(0.0048)
    assert rows["legacy-provider"]["estimated_api_cost_usd"] == pytest.approx(0.0048)

    report = runner.invoke(app, [*args, "report", "--run", "r1", "--format", "json"])
    assert report.exit_code == 0, report.output
    row = json.loads(report.output)["groups"][0]
    assert row["estimated_api_cost_usd"] == pytest.approx(0.0144)
    assert (row["estimated_api_cost_samples"], row["trials"]) == (3, 4)

    alias = runner.invoke(
        app, [*args, "report", "--run", "r1", "--by", "harness_model", "--format", "json"]
    )
    # Harness comparisons intentionally refuse unknown providers, unlike stack reports.
    assert alias.exit_code == 1
    store.save_trial(item.model_copy(update={"trial_id": "legacy-provider"}))
    alias = runner.invoke(
        app, [*args, "report", "--run", "r1", "--by", "harness_model", "--format", "json"]
    )
    assert alias.exit_code == 0, alias.output
    group = json.loads(alias.output)["groups"][0]
    assert group["value"] == "codex + gpt-6-luna"
    assert group["estimated_api_cost_usd"] == pytest.approx(0.0144)


def test_report_uses_the_official_rate_for_each_model() -> None:
    glm = trial("r1", "glm", "commandcode", TrialStatus.PASSED)
    glm.agent.requested_model = glm.agent.resolved_model = "z-ai/glm-5.3-flash"
    glm.agent.provider = "commandcode"
    tokens = glm.metrics.usage.tokens
    source = MetricSource.AGENT_REPORTED
    tokens.input_tokens = Metric[int].of(100_000, source)
    tokens.cached_input_tokens = Metric[int].of(80_000, source)
    tokens.output_tokens = Metric[int].of(4_000, source)

    deepseek = glm.model_copy(deep=True, update={"trial_id": "deepseek"})
    deepseek.agent.resolved_model = "deepseek/deepseek-v4.1-flash-1017"
    deepseek.agent.reported_model = Metric[str].of(deepseek.agent.resolved_model, source)
    unknown = glm.model_copy(deep=True, update={"trial_id": "unknown"})
    unknown.agent.requested_model = unknown.agent.resolved_model = "z-ai/glm-5.3-flashx"
    trials = [glm, deepseek, unknown]

    glm_row, deepseek_row, unknown_row = map(
        json.loads, export_trials(trials, "jsonl").splitlines()
    )
    assert glm_row["estimated_api_cost_usd"] == pytest.approx(0.0074)
    assert deepseek_row["estimated_api_cost_usd"] == pytest.approx(0.01128)
    assert deepseek_row["estimated_api_cost_model_assumed"] is False
    assert unknown_row["estimated_api_cost_usd"] is None

    dimension = ComparisonDimension.MODEL
    report = render_report(compare_trials(trials, dimension), dimension, "json", trials)
    groups = {group["value"]: group for group in json.loads(report)["groups"]}
    for item, exported in zip(trials, (glm_row, deepseek_row, unknown_row), strict=True):
        group = groups[item.agent.model_label]
        assert group["estimated_api_cost_usd"] == exported["estimated_api_cost_usd"]
        assert group["estimated_api_cost_rate_card"] == exported["estimated_api_cost_rate_card"]


def test_opencode_estimates_reasoning_at_the_output_rate() -> None:
    item = trial("r1", "opencode", "opencode", TrialStatus.PASSED)
    item.agent.requested_model = "openai/gpt-6-luna"
    item.agent.provider = "openai"
    tokens = item.metrics.usage.tokens
    source = MetricSource.AGENT_REPORTED
    tokens.input_tokens = Metric[int].of(100_000, source)
    tokens.cached_input_tokens = Metric[int].of(80_000, source)
    tokens.output_tokens = Metric[int].of(4_000, source)
    tokens.reasoning_tokens = Metric[int].of(2_000, source)

    # (100k - 80k) uncached input + 80k cached + (4k output + 2k reasoning).
    row = json.loads(export_trials([item], "jsonl"))
    assert row["estimated_api_cost_usd"] == pytest.approx(0.0058)
    assert row["estimated_api_cost_model_assumed"] is True

    dimension = ComparisonDimension.HARNESS
    report = json.loads(render_report(compare_trials([item], dimension), dimension, "json", [item]))
    group = report["groups"][0]
    assert group["value"] == "opencode"
    assert group["estimated_api_cost_usd"] == pytest.approx(0.0058)
    assert group["estimated_api_cost_samples"] == 1

    # OpenCode's output excludes reasoning, so a missing reasoning count makes
    # the output total unknown rather than zero.
    tokens.reasoning_tokens = Metric[int].unavailable()
    assert json.loads(export_trials([item], "jsonl"))["estimated_api_cost_usd"] is None

    # A reported zero is a real count and remains priceable.
    tokens.reasoning_tokens = Metric[int].of(0, source)
    assert json.loads(export_trials([item], "jsonl"))["estimated_api_cost_usd"] == pytest.approx(
        0.0048
    )


def test_report_rejects_mixed_task_versions(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    a = trial("r1", "a", "codex", TrialStatus.PASSED)
    b = trial("r1", "b", "codex", TrialStatus.PASSED)
    b.task_version = "2"
    store.save_trial(a)
    store.save_trial(b)
    result = CliRunner().invoke(app, ["--results-dir", str(tmp_path), "report", "--run", "r1"])
    assert result.exit_code == 1 and "task/version" in result.output


def test_markdown_report_escapes_group_label() -> None:
    item = trial("r1", "t1", "codex", TrialStatus.PASSED)
    item.environment.stack = "bad|stack\nnext"
    rows = compare_trials([item], ComparisonDimension.STACK)
    report = render_report(rows, ComparisonDimension.STACK, "markdown", [item])
    assert "bad&#124;stack next" in report
    assert "| bad|stack" not in report


def test_csv_neutralizes_formula_labels_without_changing_numbers() -> None:
    output = csv_text([{"label": "=1+1", "spaced": "  +cmd", "tab": "\tcmd", "count": -3}])
    row = next(csv.DictReader(io.StringIO(output)))
    assert row == {"label": "'=1+1", "spaced": "'  +cmd", "tab": "'\tcmd", "count": "-3"}


def test_report_json_rejects_nonfinite_aggregate() -> None:
    item = trial("r1", "t1", "codex", TrialStatus.PASSED)
    row = compare_trials([item], ComparisonDimension.STACK)[0]
    with pytest.raises(ValueError):
        render_report(
            [replace(row, mean_seconds=float("nan"))], ComparisonDimension.STACK, "json", [item]
        )


def test_report_filters_mixed_modes_and_auth(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    store.save_manifest(manifest("r2"))
    a = trial("r1", "a", "codex", TrialStatus.PASSED)
    b = trial("r2", "b", "codex", TrialStatus.PASSED)
    b.agent.auth_mode = AuthMode.API_KEY
    b.tooling.mode = ToolingMode.BEST_ECOSYSTEM
    store.save_trial(a)
    store.save_trial(b)
    args = ["--results-dir", str(tmp_path), "report", "--format", "json"]
    runner = CliRunner()
    assert runner.invoke(app, args).exit_code == 1
    result = runner.invoke(app, [*args, "--auth", "subscription", "--mode", "standard"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["groups"][0]["trials"] == 1
