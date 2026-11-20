"""Write the site export (docs/phase-5/schemas.md#site-export)."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_benchmark import __version__
from ai_benchmark.config.loader import SpecCatalog
from ai_benchmark.errors import BenchmarkError
from ai_benchmark.reporting import trial_row
from ai_benchmark.site.load import Skipped, load_bundles
from ai_benchmark.site.models import (
    EXPORT_SCHEMA,
    SCHEMAS,
    TRIALS,
    ExportInfo,
    Meta,
    MetaTask,
    Pages,
    TrialRow,
)
from ai_benchmark.site.pages import SiteData, build_pages


class SiteExportError(BenchmarkError):
    """The export cannot be written."""


@dataclass(frozen=True)
class ExportSummary:
    runs: int
    trials: int
    pages: int
    skipped: list[Skipped]


def data_commit(data_dir: Path) -> str | None:
    """``HEAD`` of ``data_dir`` when it is itself the root of a git repository, else None."""

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", "-C", str(data_dir), *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    top = git("rev-parse", "--show-toplevel")
    if top is None or Path(top).resolve() != data_dir.resolve():
        return None
    return git("rev-parse", "HEAD")


def _dump(file: Path, data: Any) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
    file.write_text(text + "\n", encoding="utf-8")


def export_site(
    data_dir: Path,
    out_dir: Path,
    base_url: str,
    catalog: SpecCatalog,
    *,
    built_at: datetime,
    platform_git_commit: str | None,
) -> ExportSummary:
    """Build the whole export in memory, then empty ``out_dir`` and write it.

    Nothing is written when building fails (for example on a page path collision).
    """
    data_dir, out_dir = data_dir.resolve(), out_dir.resolve()
    if not (data_dir / "submissions").is_dir():
        raise SiteExportError(f"no submissions/ folder in {data_dir}")
    if out_dir == data_dir or out_dir in data_dir.parents:
        raise SiteExportError(f"--out {out_dir} would delete the data folder {data_dir}")

    bundles, skipped = load_bundles(data_dir, catalog)
    specs = catalog.tasks()
    site = SiteData(bundles, specs)
    pages = Pages(pages=build_pages(site))
    rows = [
        TrialRow(
            **trial_row(trial),
            verified=bundle.verified,
            logs_available=bundle.submission.logs_included,
            suite_sha256=bundle.submission.acceptance_suite_sha256,
            submitted_at=bundle.submission.submitted_at,
            submitter=bundle.submission.submitter.github or bundle.submission.submitter.name,
        )
        for bundle in bundles
        for trial in bundle.trials
    ]
    versions = sorted({(trial.task_id, trial.task_version) for trial in site.trials})
    meta = Meta(
        runs=len(bundles),
        trials=len(rows),
        tasks=[
            MetaTask(
                id=task,
                version=version,
                name=specs[task].name if task in specs else None,
                description=specs[task].description.strip() if task in specs else None,
            )
            for task, version in versions
        ],
    )
    info = ExportInfo(
        export_schema=EXPORT_SCHEMA,
        built_at=built_at,
        platform_version=__version__,
        platform_git_commit=platform_git_commit,
        data_commit=data_commit(data_dir),
        base_url=base_url,
    )

    if out_dir.exists():
        for child in out_dir.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    _dump(out_dir / "export.json", info.model_dump(mode="json"))
    _dump(out_dir / "data" / "trials.json", TRIALS.dump_python(rows, mode="json"))
    _dump(out_dir / "data" / "meta.json", meta.model_dump(mode="json"))
    _dump(out_dir / "pages.json", pages.model_dump(mode="json"))
    for name, adapter in SCHEMAS.items():
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema"}
        schema.update(adapter.json_schema())
        _dump(out_dir / "schema" / f"{name}.schema.json", schema)
    for bundle in bundles:
        target = out_dir / "runs" / bundle.submission.run_id
        target.mkdir(parents=True, exist_ok=True)
        for name in ("submission.json", "manifest.json"):
            shutil.copyfile(bundle.path / name, target / name)
    return ExportSummary(len(bundles), len(rows), len(pages.pages), skipped)
