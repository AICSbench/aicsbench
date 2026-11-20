from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_benchmark.cli.app import app

from .conftest import REPO_ROOT

runner = CliRunner()
DIRS = [
    "--tasks-dir",
    str(REPO_ROOT / "benchmarks" / "tasks"),
    "--stacks-dir",
    str(REPO_ROOT / "benchmarks" / "stacks"),
]


def invoke(*args: str, results: Path | None = None) -> "object":
    extra = ["--results-dir", str(results)] if results else []
    return runner.invoke(app, [*DIRS, *extra, *args])


def test_listing_commands() -> None:
    for cmd, needle in [("tasks", "task-management-api"), ("stacks", "fastapi"), ("agents", "")]:
        result = invoke(cmd)
        assert result.exit_code == 0, result.output
        assert needle in result.output


def test_version_uses_command_name() -> None:
    result = invoke("version")
    assert result.exit_code == 0
    assert result.output.startswith("aicsbench ")


def test_validate_catalog_and_files() -> None:
    assert invoke("validate").exit_code == 0
    assert invoke("validate", str(REPO_ROOT / "examples" / "experiment.yaml")).exit_code == 0


def test_validate_reports_bad_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: x\nlanguage: python\n")
    result = invoke("validate", str(bad))
    assert result.exit_code == 1


def test_run_dry_run_shows_plan() -> None:
    result = invoke(
        "run",
        "--agents",
        "codex,claude:m1",
        "--task",
        "task-management-api",
        "--stacks",
        "fastapi,laravel",
        "--trials",
        "2",
        "--seed",
        "1",
        "--dry-run",
    )
    assert result.exit_code == 0, result.output
    assert "8 trial(s)" in result.output


def test_run_with_unknown_adapter_fails_preflight() -> None:
    result = invoke(
        "run", "--agents", "missing-agent", "--task", "task-management-api", "--stacks", "fastapi"
    )
    assert result.exit_code == 1


def test_run_requires_arguments() -> None:
    assert invoke("run").exit_code == 2


def test_results_empty(tmp_path: Path) -> None:
    result = invoke("results", results=tmp_path)
    assert result.exit_code == 0 and "No results" in result.output


def test_run_dry_run_auth_mode() -> None:
    args = ["run", "--agents", "codex", "--task", "task-management-api", "--stacks", "fastapi"]
    assert "auth subscription" in invoke(*args, "--dry-run").output
    result = invoke(*args, "--auth", "api_key", "--dry-run")
    assert result.exit_code == 0 and "auth api_key" in result.output


def test_run_dry_run_repair_budget() -> None:
    result = invoke(
        "run",
        "--agents",
        "codex",
        "--task",
        "task-management-api",
        "--stacks",
        "fastapi",
        "--repairs",
        "2",
        "--dry-run",
    )
    assert result.exit_code == 0
    assert "repairs 2" in result.output


def test_run_dry_run_parallelism() -> None:
    result = invoke(
        "run",
        "--agents",
        "codex",
        "--task",
        "task-management-api",
        "--stacks",
        "fastapi",
        "--parallel",
        "2",
        "--dry-run",
    )
    assert result.exit_code == 0
    assert "parallel 2" in result.output


def test_run_dry_run_shows_the_resolved_stack_version() -> None:
    result = invoke(
        "run",
        "--agents",
        "codex",
        "--task",
        "task-management-api",
        "--stacks",
        "express-latest@5.2.1,express-latest,express",
        "--trials",
        "1",
        "--dry-run",
    )
    assert result.exit_code == 0, result.output
    assert "3 trial(s)" in result.output
    for label in ("express-latest@5.2.1", "express-latest@5.3.0", "express@4.21"):
        assert label in result.output


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ("express-latest@^5.2", "must be an exact x.y.z version"),
        ("express-latest@5.2.x", "must be an exact x.y.z version"),
        ("express-latest@5.2", "must be an exact x.y.z version"),
        ("express-latest@latest", "must be an exact x.y.z version"),
        ("express-latest@5.2;rm -rf /", "must be an exact x.y.z version"),
        ("express@1.0.0", "stack 'express' does not accept a framework version"),
    ],
)
def test_run_rejects_a_bad_stack_version_before_anything_runs(entry: str, message: str) -> None:
    # No --dry-run: a selection that got through would plan and try to run.
    result = invoke("run", "--agents", "codex", "--task", "task-management-api", "--stacks", entry)
    assert result.exit_code == 2, result.output
    assert message in " ".join(result.output.split())
    assert "trial id" not in result.output and "Run " not in result.output
