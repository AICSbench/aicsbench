"""Read the accepted bundles of an ``aicsbench/data`` checkout."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ai_benchmark.config.loader import SpecCatalog
from ai_benchmark.models.results import RunManifest, TrialResult
from ai_benchmark.submission.models import Problem, SubmissionInfo
from ai_benchmark.submission.validate import validate_bundle

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Bundle:
    path: Path
    submission: SubmissionInfo
    manifest: RunManifest
    trials: tuple[TrialResult, ...]
    verified: bool


@dataclass(frozen=True)
class Skipped:
    path: Path
    problems: tuple[Problem, ...]


def read_verified(data_dir: Path) -> set[str]:
    """Run IDs in ``verified-runs.txt``: one per line, ``#`` starts a comment."""
    file = data_dir / "verified-runs.txt"
    if not file.is_file():
        return set()
    lines = (line.split("#", 1)[0].strip() for line in file.read_text("utf-8").splitlines())
    return {line for line in lines if line}


def load_bundles(data_dir: Path, catalog: SpecCatalog) -> tuple[list[Bundle], list[Skipped]]:
    """Every bundle under ``submissions/<task_id>/<run_id>/`` that validates in rebuild mode.

    A bundle that fails validation is skipped with a warning.
    """
    verified = read_verified(data_dir)
    root = data_dir / "submissions"
    folders = sorted(
        run
        for task in (root.iterdir() if root.is_dir() else ())
        if task.is_dir()
        for run in task.iterdir()
        if run.is_dir()
    )
    bundles: list[Bundle] = []
    skipped: list[Skipped] = []
    for folder in folders:
        problems = validate_bundle(folder, catalog, check_catalog=False)
        if problems:
            log.warning(
                "skipping invalid bundle %s: %s", folder, "; ".join(str(p) for p in problems)
            )
            skipped.append(Skipped(folder, tuple(problems)))
            continue
        submission = SubmissionInfo.model_validate_json((folder / "submission.json").read_bytes())
        manifest = RunManifest.model_validate_json((folder / "manifest.json").read_bytes())
        trials = tuple(
            TrialResult.model_validate_json((folder / "trials" / f"{trial_id}.json").read_bytes())
            for trial_id in manifest.trial_ids
        )
        bundles.append(Bundle(folder, submission, manifest, trials, submission.run_id in verified))
    return bundles, skipped
