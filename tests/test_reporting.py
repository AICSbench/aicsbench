import csv
import importlib.util
import io
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_benchmark import reporting
from ai_benchmark.cli.app import app
from ai_benchmark.comparison import compare_trials
from ai_benchmark.models import (
    AuthMode,
    ComparisonDimension,
    Metric,
    MetricSource,
    ToolingMode,
    TrialResult,
    TrialStatus,
)
from ai_benchmark.pricing import SOURCE, Catalog, ModelPrice, default_catalog, load_catalog
from ai_benchmark.reporting import csv_text, export_trials, render_report
from ai_benchmark.storage import JsonResultStore, ResultFilter

from .test_storage import manifest, trial

SNAPSHOT_TIME = "2026-11-21T11:37:56Z"


@pytest.fixture(scope="module")
def pricing_snapshot() -> Catalog:
    # A refresh can change model coverage and prices without changing report behavior.
    repo = Path(__file__).resolve().parents[1]
    return load_catalog(repo / "src/ai_benchmark/pricing/openrouter-models-2026-11-21.json")


@pytest.fixture(autouse=True)
def pin_report_prices(monkeypatch: pytest.MonkeyPatch, pricing_snapshot: Catalog) -> None:
    monkeypatch.setattr(reporting, "default_catalog", lambda: pricing_snapshot)


def _priced_trial(model: str, *, input_tokens: int, cached: int, output: int) -> TrialResult:
    """A Codex trial with OpenAI-style token accounting for pricing tests."""
    item = trial("r1", model.replace("/", "-"), "codex", TrialStatus.PASSED)
    item.agent.requested_model = item.agent.resolved_model = model
    tokens = item.metrics.usage.tokens
    tokens.accounting_scheme = "openai"
    source = MetricSource.AGENT_REPORTED
    tokens.input_tokens = Metric[int].of(input_tokens, source)
    tokens.cached_input_tokens = Metric[int].of(cached, source)
    tokens.output_tokens = Metric[int].of(output, source)
    return item


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


def test_report_exposes_timing_spread_in_json_csv_and_markdown() -> None:
    def timed(
        trial_id: str,
        seconds: float | None,
        *,
        status: TrialStatus = TrialStatus.PASSED,
        limited: bool = False,
    ) -> TrialResult:
        item = trial("r1", trial_id, "codex", status, limited=limited)
        if seconds is not None:
            item.metrics.performance.agent_execution_seconds = Metric[float].of(
                seconds, MetricSource.MEASURED
            )
        return item

    trials = [
        timed("two", 2.0),
        timed("four", 4.0),
        timed("hundred", 100.0),
        timed("failed", 1.0, status=TrialStatus.FAILED),
        timed("limited", 999.0, limited=True),
        timed("no-timer", None),
    ]
    rows = compare_trials(trials, ComparisonDimension.STACK)

    report = json.loads(render_report(rows, ComparisonDimension.STACK, "json", trials))
    group = report["groups"][0]
    assert (
        group["timed"],
        group["median_seconds"],
        group["min_seconds"],
        group["max_seconds"],
    ) == (3, 4.0, 2.0, 100.0)

    csv_row = next(
        csv.DictReader(io.StringIO(render_report(rows, ComparisonDimension.STACK, "csv", trials)))
    )
    assert (
        csv_row["timed"],
        csv_row["median_seconds"],
        csv_row["min_seconds"],
        csv_row["max_seconds"],
    ) == ("3", "4.0", "2.0", "100.0")

    markdown = render_report(rows, ComparisonDimension.STACK, "markdown", trials)
    assert (
        "| Timed | Mean time (s) | Std dev (s) | Median time (s) | Min time (s) | "
        "Max time (s) |" in markdown
    )
    line = next(line for line in markdown.splitlines() if line.startswith("| fastapi |"))
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    assert cells[:9] == ["fastapi", "5/6", "1", "3", "35.33", "56.01", "4.00", "2.00", "100.00"]


def test_report_shows_unavailable_timing_without_eligible_samples() -> None:
    failed = trial("r1", "failed", "codex", TrialStatus.FAILED)
    failed.metrics.performance.agent_execution_seconds = Metric[float].of(
        1.0, MetricSource.MEASURED
    )
    limited = trial("r1", "limited", "codex", TrialStatus.PASSED, limited=True)
    limited.metrics.performance.agent_execution_seconds = Metric[float].of(
        999.0, MetricSource.MEASURED
    )
    no_timer = trial("r1", "no-timer", "codex", TrialStatus.PASSED)
    trials = [failed, limited, no_timer]
    rows = compare_trials(trials, ComparisonDimension.STACK)

    report = json.loads(render_report(rows, ComparisonDimension.STACK, "json", trials))
    group = report["groups"][0]
    assert (
        group["timed"],
        group["median_seconds"],
        group["min_seconds"],
        group["max_seconds"],
    ) == (0, None, None, None)

    csv_row = next(
        csv.DictReader(io.StringIO(render_report(rows, ComparisonDimension.STACK, "csv", trials)))
    )
    assert (csv_row["median_seconds"], csv_row["min_seconds"], csv_row["max_seconds"]) == (
        "",
        "",
        "",
    )

    markdown = render_report(rows, ComparisonDimension.STACK, "markdown", trials)
    line = next(line for line in markdown.splitlines() if line.startswith("| fastapi |"))
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    assert cells[3:9] == ["0", "n/a", "n/a", "n/a", "n/a", "n/a"]


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
    assert (
        rows["priced"]["estimated_api_cost_rate_card"]
        == f"openrouter/openai/gpt-6-luna/base@{SNAPSHOT_TIME}"
    )
    assert (
        rows["priced"]["estimated_api_cost_price_source"] == "https://openrouter.ai/api/v1/models"
    )
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


def test_report_uses_openrouter_snapshot_rates() -> None:
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
    unknown.agent.requested_model = unknown.agent.resolved_model = "z-ai/glm-5.3-flash-not-listed"
    trials = [glm, deepseek, unknown]

    glm_row, deepseek_row, unknown_row = map(
        json.loads, export_trials(trials, "jsonl").splitlines()
    )
    assert glm_row["estimated_api_cost_usd"] == pytest.approx(0.0035116)
    assert deepseek_row["estimated_api_cost_usd"] == pytest.approx(0.00564)
    assert deepseek_row["estimated_api_cost_model_assumed"] is False
    assert unknown_row["estimated_api_cost_usd"] is None
    assert (
        glm_row["estimated_api_cost_rate_card"]
        == f"openrouter/z-ai/glm-5.3-flash/base@{SNAPSHOT_TIME}"
    )
    assert (
        deepseek_row["estimated_api_cost_rate_card"]
        == f"openrouter/deepseek/deepseek-v4.1-flash-1017/base@{SNAPSHOT_TIME}"
    )
    assert glm_row["estimated_api_cost_price_source"] == "https://openrouter.ai/api/v1/models"

    dimension = ComparisonDimension.MODEL
    report = render_report(compare_trials(trials, dimension), dimension, "json", trials)
    groups = {group["value"]: group for group in json.loads(report)["groups"]}
    for item, exported in zip(trials, (glm_row, deepseek_row, unknown_row), strict=True):
        group = groups[item.agent.model_label]
        assert group["estimated_api_cost_usd"] == exported["estimated_api_cost_usd"]
        assert group["estimated_api_cost_rate_card"] == exported["estimated_api_cost_rate_card"]


def test_openrouter_snapshot_keeps_variants_distinct(pricing_snapshot: Catalog) -> None:
    catalog = pricing_snapshot
    assert (catalog.retrieved_at, catalog.source) == (SNAPSHOT_TIME, SOURCE)
    assert len(catalog.models) == 476
    assert {
        "openai/gpt-6-luna",
        "openai/gpt-6-luna:batch",
        "openai/gpt-6.1-terra",
        "openai/gpt-6-sol",
        "anthropic/claude-opus-5.5",
        "anthropic/claude-sonnet-5.5",
        "minimax/minimax-m3.1-flash",
        "deepseek/deepseek-v4.1-flash",
        "deepseek/deepseek-v4.1-flash-1017",
    } <= set(catalog.models)

    luna = catalog.lookup("openai/gpt-6-luna")
    batch = catalog.lookup("openai/gpt-6-luna:batch")
    base = catalog.lookup("deepseek/deepseek-v4.1-flash")
    dated = catalog.lookup("deepseek/deepseek-v4.1-flash-1017")
    assert luna is not None and batch is not None
    assert base is not None and dated is not None
    assert luna.prompt == pytest.approx(0.0000001)
    assert batch.prompt == pytest.approx(0.00000005)
    assert base.prompt != dated.prompt
    router = catalog.lookup("openrouter/auto")
    assert router is not None and router.is_priceable is False


def test_default_catalog_loads_latest_bundled_snapshot() -> None:
    directory = Path(__file__).resolve().parents[1] / "src/ai_benchmark/pricing"
    latest = max(directory.glob("openrouter-models-*.json"))
    assert default_catalog() == load_catalog(latest)


def test_refresh_snapshot_keeps_text_models_and_base_rates() -> None:
    path = Path(__file__).resolve().parents[1] / "scripts" / "refresh_openrouter_models.py"
    spec = importlib.util.spec_from_file_location("refresh_openrouter_models", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    payload = {
        "data": [
            {
                "id": "z/model",
                "name": "Z",
                "context_length": 100,
                "architecture": {
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                },
                "pricing": {
                    "prompt": "0.1",
                    "completion": "0.2",
                    "input_cache_read": "0.01",
                    "input_cache_write": "0.02",
                    "web_search": "9",
                    "overrides": [{"min_prompt_tokens": 1}],
                },
            },
            {
                "id": "a/model",
                "name": "A",
                "context_length": 100,
                "architecture": {
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                },
                "pricing": {"prompt": "1", "completion": "2"},
            },
            {
                "id": "a/image",
                "name": "A image",
                "context_length": 100,
                "architecture": {
                    "input_modalities": ["text"],
                    "output_modalities": ["image"],
                },
                "pricing": {"prompt": "1", "completion": "2"},
            },
        ]
    }
    retrieved_at = datetime(2026, 11, 21, tzinfo=timezone.utc)
    snapshot = module.build_snapshot(payload, retrieved_at)
    assert snapshot["model_count"] == 2
    assert [model["id"] for model in snapshot["models"]] == ["a/model", "z/model"]
    assert snapshot["models"][1]["pricing"] == {
        "prompt": "0.1",
        "completion": "0.2",
        "input_cache_read": "0.01",
    }
    assert snapshot["retrieved_at"] == "2026-11-21T00:00:00Z"
    with pytest.raises(ValueError):
        module.build_snapshot({"data": []}, retrieved_at)


def test_openrouter_lookup_uses_exact_ids_or_one_bare_id(pricing_snapshot: Catalog) -> None:
    catalog = pricing_snapshot
    assert catalog.lookup("gpt-6-luna").model_id == "openai/gpt-6-luna"
    assert catalog.lookup("GPT-6-LUNA").model_id == "openai/gpt-6-luna"
    assert catalog.lookup("gpt-6-luna:batch").model_id == "openai/gpt-6-luna:batch"
    assert (
        catalog.lookup("deepseek-v4.1-flash-1017").model_id == "deepseek/deepseek-v4.1-flash-1017"
    )
    assert catalog.lookup("deepseek/deepseek-v4.1-flash").model_id == "deepseek/deepseek-v4.1-flash"
    # Removed aliases and unlisted IDs are not guessed.
    assert catalog.lookup("deepseek-flash") is None
    assert catalog.lookup("anthropic/claude-opus-latest") is None
    assert catalog.lookup("glm-5.3-flash-not-listed") is None

    def price(model_id: str) -> ModelPrice:
        return ModelPrice(model_id, 1e-6, 2e-6, None, "2026-11-21")

    ambiguous = Catalog(
        "2026-11-21", {"a/shared": price("a/shared"), "b/shared": price("b/shared")}
    )
    assert ambiguous.lookup("shared") is None
    assert ambiguous.lookup("a/shared").model_id == "a/shared"


def test_openrouter_estimate_uses_base_rates_not_overrides() -> None:
    # 300k input crosses GPT-6 Luna's 272k context override, which is not applied.
    luna = _priced_trial("gpt-6-luna", input_tokens=300_000, cached=0, output=1_000)
    row = json.loads(export_trials([luna], "jsonl"))
    assert row["estimated_api_cost_usd"] == pytest.approx(0.0305)
    assert (
        row["estimated_api_cost_rate_card"] == f"openrouter/openai/gpt-6-luna/base@{SNAPSHOT_TIME}"
    )

    # DeepSeek's weekday peak window would double the input rate; the base rate is fixed.
    deepseek = _priced_trial(
        "deepseek/deepseek-v4.1-flash-1017", input_tokens=100_000, cached=0, output=1_000
    )
    row = json.loads(export_trials([deepseek], "jsonl"))
    assert row["estimated_api_cost_usd"] == pytest.approx(0.0156)


def test_openrouter_prices_exact_variant_ids_separately() -> None:
    base = _priced_trial("openai/gpt-6-luna", input_tokens=100_000, cached=0, output=1_000)
    batch = _priced_trial("openai/gpt-6-luna:batch", input_tokens=100_000, cached=0, output=1_000)
    base_row, batch_row = map(json.loads, export_trials([base, batch], "jsonl").splitlines())
    assert base_row["estimated_api_cost_usd"] == pytest.approx(0.0105)
    assert batch_row["estimated_api_cost_usd"] == pytest.approx(0.00525)
    assert base_row["estimated_api_cost_rate_card"] != batch_row["estimated_api_cost_rate_card"]


def test_openrouter_estimate_needs_a_cache_rate_only_for_cached_tokens() -> None:
    item = _priced_trial("minimax/minimax-m2.7", input_tokens=100_000, cached=0, output=1_000)
    row = json.loads(export_trials([item], "jsonl"))
    assert row["estimated_api_cost_usd"] == pytest.approx(0.0618)

    tokens = item.metrics.usage.tokens
    tokens.cached_input_tokens = Metric[int].of(1_000, MetricSource.AGENT_REPORTED)
    assert json.loads(export_trials([item], "jsonl"))["estimated_api_cost_usd"] is None

    tokens.cached_input_tokens = Metric[int].unavailable()
    assert json.loads(export_trials([item], "jsonl"))["estimated_api_cost_usd"] is None


def test_openrouter_estimate_rejects_unknown_ids_and_accounting() -> None:
    for model in ("deepseek-flash", "openai/gpt-6-luna-latest", "openrouter/auto"):
        item = _priced_trial(model, input_tokens=100_000, cached=0, output=1_000)
        assert json.loads(export_trials([item], "jsonl"))["estimated_api_cost_usd"] is None

    item = _priced_trial("gpt-6-luna", input_tokens=100_000, cached=0, output=1_000)
    item.metrics.usage.tokens.accounting_scheme = "anthropic"
    assert json.loads(export_trials([item], "jsonl"))["estimated_api_cost_usd"] is None


def test_report_labels_openrouter_base_baseline() -> None:
    item = _priced_trial("gpt-6-luna", input_tokens=100_000, cached=80_000, output=4_000)
    rows = compare_trials([item], ComparisonDimension.STACK)
    report = render_report(rows, ComparisonDimension.STACK, "markdown", [item])
    assert "OpenRouter listed base prompt, completion and cache-read rates" in report
    assert "not an official vendor price or an actual bill" in report
    assert "Route, time-of-day and context-length overrides" in report
    assert "non-token fees are not applied" in report
    assert "aliases are not guessed" in report
    assert f"openrouter/openai/gpt-6-luna/base@{SNAPSHOT_TIME}" in report


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
