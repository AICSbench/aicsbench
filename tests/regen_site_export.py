"""Regenerate the committed site export fixture.

    python -m tests.regen_site_export

writes the fixture data folder (``tests/fixtures/site-data/``) and its export
(``tests/fixtures/site-export/``). ``aicsbench/web`` copies the export for its
own tests, so a change here is a change to that contract. The helpers also
build bundles for the site export tests.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_benchmark.config.loader import SpecCatalog
from ai_benchmark.config.settings import PlatformSettings
from ai_benchmark.models import Metric, MetricSource, TrialStatus
from ai_benchmark.models.enums import AuthMode, ToolingMode
from ai_benchmark.models.results import (
    AgentRecord,
    EnvironmentRecord,
    RunManifest,
    ToolingRecord,
    TrialResult,
)
from ai_benchmark.site.export import export_site

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "tests" / "fixtures" / "site-data"
EXPORT = REPO_ROOT / "tests" / "fixtures" / "site-export"
BASE_URL = "https://aicsbench.github.io/web/"
BUILT_AT = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
SUITES = json.loads((REPO_ROOT / "tests" / "fixtures" / "suite-hashes.json").read_text())
XSS = "<script>alert(1)</script>"
STACKS = {
    "fastapi": ("python", "3.13", "fastapi", "0.129"),
    "laravel": ("php", "8.4", "laravel", "12"),
}


def make_trial(
    run_id: str,
    index: int,
    *,
    task: str = "task-management-api",
    version: str = "2",
    harness: str = "codex",
    harness_version: str = "0.167.2",
    model: str = "gpt-6-luna",
    stack: str = "fastapi",
    status: TrialStatus = TrialStatus.PASSED,
    seconds: float | None = 600.0,
) -> TrialResult:
    language, language_version, framework, framework_version = STACKS.get(
        stack, ("python", "3.13", "fastapi", "0.129")
    )
    trial = TrialResult(
        trial_id=f"trial-{index:03d}",
        run_id=run_id,
        task_id=task,
        task_version=version,
        trial_index=index - 1,
        execution_order=index - 1,
        status=status,
        agent=AgentRecord(
            name=harness,
            version=harness_version,
            requested_model=model,
            auth_mode=AuthMode.SUBSCRIPTION,
            resolved_model=model,
        ),
        environment=EnvironmentRecord(
            stack=stack,
            language=language,
            language_version=language_version,
            framework=framework,
            framework_version=framework_version,
            installed_versions={"language": f"{language_version}.1"},
        ),
        tooling=ToolingRecord(mode=ToolingMode.STANDARD),
        started_at=datetime(2026, 11, 17, 7, tzinfo=timezone.utc) + timedelta(hours=index),
    )
    if "provider" in AgentRecord.model_fields:
        # Task 9 refuses harness comparisons over trials without a provider.
        trial.agent.provider = AgentRecord.default_provider(harness, model)  # type: ignore[attr-defined]
    if seconds is not None:
        trial.metrics.performance.agent_execution_seconds = Metric[float].of(
            seconds, MetricSource.MEASURED
        )
    return trial


def write_bundle(
    data: Path,
    trials: list[TrialResult],
    *,
    suite: str | None = None,
    submitter: str = "Fixture Submitter",
    github: str | None = None,
    notes: str = "",
    verified: bool = False,
    submitted_at: datetime | None = None,
) -> Path:
    """Write one valid bundle under ``data/submissions/`` (and list it as verified)."""
    first = trials[0]
    task, version, run_id = first.task_id, first.task_version, first.run_id
    suite = suite or SUITES.get(f"{task}@{version}", "0" * 64)
    bundle = data / "submissions" / task / run_id
    (bundle / "trials").mkdir(parents=True)
    manifest = RunManifest(
        run_id=run_id,
        created_at=datetime(2026, 11, 17, 6, 45, tzinfo=timezone.utc),
        platform_version="0.1.0",
        platform_git_commit="3547a18",
        seed=7,
        auth_mode=AuthMode.SUBSCRIPTION,
        experiment={"task": task},
        task_snapshot={"id": task, "version": version},
        trial_ids=[trial.trial_id for trial in trials],
        acceptance_suite_sha256=suite,
    )
    files = {"manifest.json": manifest.model_dump_json(indent=2) + "\n"}
    for trial in trials:
        files[f"trials/{trial.trial_id}.json"] = trial.model_dump_json(indent=2) + "\n"
    for relative, text in files.items():
        (bundle / relative).write_text(text, encoding="utf-8")
    submission = {
        "schema_version": 1,
        "run_id": run_id,
        "task_id": task,
        "task_version": version,
        "submitted_at": (submitted_at or datetime(2026, 11, 18, tzinfo=timezone.utc))
        .isoformat()
        .replace("+00:00", "Z"),
        "submitter": {"name": submitter, "github": github},
        "notes": notes,
        "platform_version": "0.1.0",
        "platform_git_commit": "3547a18",
        "acceptance_suite_sha256": suite,
        "suite_hash_source": "run",
        "logs_included": False,
        "files": {
            relative: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for relative, text in sorted(files.items())
        },
    }
    (bundle / "submission.json").write_text(json.dumps(submission, indent=2) + "\n", "utf-8")
    if verified:
        with (data / "verified-runs.txt").open("a", encoding="utf-8") as file:
            file.write(run_id + "\n")
    return bundle


def write_fixture_data(data: Path) -> None:
    """Two bundles: one verified, one community; two harnesses; two stacks.

    The verified run holds Codex + GPT (OpenAI) and Claude Code + Opus
    (Anthropic) on one stack, so every harness + model leaderboard mixes
    providers across its rows.
    """
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    (data / "verified-runs.txt").write_text("# Run IDs a maintainer verified.\n", "utf-8")
    write_bundle(
        data,
        [
            make_trial("run-verified-001", 1, seconds=600.0),
            make_trial("run-verified-001", 2, seconds=660.0),
            make_trial("run-verified-001", 3, status=TrialStatus.FAILED, seconds=900.0),
            # The same run also has Claude Code + Opus on the same stack, so the
            # harness + model leaderboards rank two providers' packages.
            make_trial(
                "run-verified-001",
                4,
                harness="claude",
                harness_version="2.1.323",
                model="claude-opus-5-5",
                seconds=480.0,
            ),
            make_trial(
                "run-verified-001",
                5,
                harness="claude",
                harness_version="2.1.323",
                model="claude-opus-5-5",
                seconds=540.0,
            ),
        ],
        submitter="Verified Submitter",
        github="verified-submitter",
        notes="Verified fixture run.",
        verified=True,
    )
    write_bundle(
        data,
        [
            make_trial(
                "run-community-001",
                index,
                harness="claude",
                harness_version="2.1.323",
                model="claude-opus-5-5",
                stack="laravel",
                status=TrialStatus.FAILED,
                seconds=700.0,
            )
            for index in (1, 2)
        ],
        submitter=XSS,
        notes=f"Community fixture run. {XSS}",
        submitted_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
    )


def catalog() -> SpecCatalog:
    return SpecCatalog(
        PlatformSettings(
            tasks_dir=REPO_ROOT / "benchmarks" / "tasks",
            stacks_dir=REPO_ROOT / "benchmarks" / "stacks",
        )
    )


def regenerate(data: Path = DATA, out: Path = EXPORT) -> None:
    write_fixture_data(data)
    export_site(data, out, BASE_URL, catalog(), built_at=BUILT_AT, platform_git_commit=None)


if __name__ == "__main__":
    regenerate()
    print(f"wrote {DATA.relative_to(REPO_ROOT)} and {EXPORT.relative_to(REPO_ROOT)}")
