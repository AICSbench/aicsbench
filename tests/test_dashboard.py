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
