"""Dashboard API uses stored trials and the shared comparison query."""

import json
from pathlib import Path

from ai_benchmark.dashboard import dashboard_response
from ai_benchmark.models import Metric, MetricSource, TrialStatus
from ai_benchmark.storage import JsonResultStore

from .test_storage import manifest, trial


def test_dashboard_serves_comparison_and_no_raw_provider_data(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    first = trial("r1", "t1", "codex", TrialStatus.PASSED)
    first.metrics.performance.agent_execution_seconds = Metric[float].of(7.5, MetricSource.MEASURED)
    first.raw = {"secret": "private-provider-output"}
    second = trial("r1", "t2", "codex", TrialStatus.FAILED)
    second.environment.stack = "express"
    store.save_trial(first)
    store.save_trial(second)

    status, _, body = dashboard_response(store, "/api/runs")
    assert status == 200
    assert json.loads(body)[0]["run_id"] == "r1"

    status, _, body = dashboard_response(store, "/api/trials?run=r1")
    assert status == 200
    assert b"private-provider-output" not in body

    status, _, body = dashboard_response(store, "/api/compare?run=r1&by=stack")
    data = json.loads(body)
    assert status == 200
    assert {group["value"] for group in data["groups"]} == {"fastapi", "express"}
    assert next(group for group in data["groups"] if group["value"] == "fastapi")[
        "mean_seconds"
    ] == 7.5


def test_dashboard_serves_harness_version_comparison_and_filter(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    for index, version in enumerate(["0.167.2", "0.167.1", None]):
        item = trial("r1", f"t{index}", "codex", TrialStatus.PASSED)
        item.agent.version = version
        item.agent.resolved_model = "gpt-6-luna"
        store.save_trial(item)

    status, _, body = dashboard_response(store, "/api/compare?run=r1&by=harness_version")
    assert status == 200
    assert {group["value"] for group in json.loads(body)["groups"]} == {
        "codex 0.167.2",
        "codex 0.167.1",
        "codex unknown",
    }

    status, _, body = dashboard_response(store, "/api/trials?run=r1&harness_version=0.167.1")
    assert status == 200
    assert [row["trial_id"] for row in json.loads(body)] == ["t1"]

    status, _, body = dashboard_response(store, "/api/trials?run=r1&harness_version=unknown")
    assert status == 200
    unknown = json.loads(body)
    assert [row["trial_id"] for row in unknown] == ["t2"]
    assert unknown[0]["harness_version"] == "unknown"

    status, _, body = dashboard_response(store, "/")
    assert status == 200
    assert b'value="harness_version"' in body
    assert b'id="harness_version"' in body


def test_dashboard_model_filter_matches_model_key_across_harnesses(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    codex = trial("r1", "t1", "codex", TrialStatus.PASSED)
    codex.agent.resolved_model = "gpt-6-luna"
    opencode = trial("r1", "t2", "opencode", TrialStatus.PASSED)
    opencode.agent.resolved_model = "openai/gpt-6-luna"
    store.save_trial(codex)
    store.save_trial(opencode)

    status, _, body = dashboard_response(store, "/api/compare?run=r1&by=harness&model=gpt-6-luna")
    assert status == 200
    assert {group["value"] for group in json.loads(body)["groups"]} == {"codex", "opencode"}

    status, _, body = dashboard_response(store, "/api/trials?run=r1")
    assert status == 200
    assert {row["model_key"] for row in json.loads(body)} == {"gpt-6-luna"}
