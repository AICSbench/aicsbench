"""Every page of the site and its precomputed leaderboards (docs/phase-5/schemas.md#pagesjson).

Leaderboard rows come from ``comparison.compare_trials`` only. A section holds
every dimension its comparison must not mix fixed: one comparability key,
one acceptance suite, verified or community runs, and the "per ..." axes of
the layout table.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass

from ai_benchmark.comparison import ComparisonRow, compare_matrix, compare_trials, version_key
from ai_benchmark.config.task import BenchmarkSpec
from ai_benchmark.models.enums import ComparisonDimension
from ai_benchmark.models.results import TrialResult
from ai_benchmark.site.load import Bundle
from ai_benchmark.site.models import (
    Link,
    Matrix,
    MatrixColumn,
    MatrixOverall,
    MatrixRow,
    Page,
    Row,
    Section,
)
from ai_benchmark.site.slugs import PathRegistry, slug
from ai_benchmark.storage.base import ResultFilter

LATEST_RUNS = 10

_AXES: dict[str, Callable[[TrialResult], str]] = {
    "stack": lambda trial: trial.environment.stack,
    "harness": lambda trial: trial.agent.name,
    "model": lambda trial: trial.agent.model_key,
}
_BY_LABEL = {
    "harness_model": "harness + model",
    "harness_version": "harness version",
}
_KEY_PARTS = ("task", "task_version", "tooling_mode", "auth_mode", "max_repairs", "max_parallel")

Leaderboard = tuple[str, tuple[str, ...]]
"""A comparison dimension and the axes each of its sections holds fixed ("per ...")."""

HOME: tuple[Leaderboard, ...] = (("harness_model", ("stack",)),)
TASK: tuple[Leaderboard, ...] = (("harness_model", ("stack",)), ("stack", ("harness", "model")))
HARNESS: tuple[Leaderboard, ...] = (
    ("model", ("stack",)),
    ("stack", ("model",)),
    ("harness_version", ("model", "stack")),
)
MODEL: tuple[Leaderboard, ...] = (("harness", ("stack",)), ("stack", ("harness",)))
STACK: tuple[Leaderboard, ...] = (("harness_model", ()),)


def to_row(row: ComparisonRow) -> Row:
    # The site schema predates the CLI-only timing spread fields and keeps its
    # fixtures stable; add them to Row before passing them through.
    return Row(
        value=row.value,
        trials=row.trials,
        passed=row.passed,
        rate_limited=row.rate_limited,
        timed=row.timed,
        mean_seconds=row.mean_seconds,
        stdev_seconds=row.stdev_seconds,
        mean_cost_usd=row.mean_cost_usd,
        cost_samples=row.cost_samples,
        low_confidence=row.low_confidence,
        success_rate=row.passed / row.trials,
    )


@dataclass
class SiteData:
    bundles: list[Bundle]
    catalog: dict[str, BenchmarkSpec]

    def __post_init__(self) -> None:
        self.by_run = {bundle.submission.run_id: bundle for bundle in self.bundles}
        self.trials = [trial for bundle in self.bundles for trial in bundle.trials]
        self.suite_by_run = {
            run_id: bundle.submission.acceptance_suite_sha256
            for run_id, bundle in self.by_run.items()
        }

    def suite(self, trial: TrialResult) -> str:
        return self.suite_by_run[trial.run_id]

    def verified(self, trial: TrialResult) -> bool:
        return self.by_run[trial.run_id].verified

    def task_title(self, task_id: str) -> str:
        spec = self.catalog.get(task_id)
        return spec.name if spec else task_id

    def heading_extras(self, key: tuple[object, ...], suite: str) -> list[str]:
        """Labels for whatever varies within this task version, so headings stay distinct."""
        same_version = [
            trial
            for trial in self.trials
            if trial.task_id == key[0] and trial.task_version == key[1]
        ]
        extras = [
            f"{_KEY_PARTS[index].replace('_', ' ')} {key[index]}"
            for index in range(2, len(_KEY_PARTS))
            if len({trial.comparability_key()[index] for trial in same_version}) > 1
        ]
        if len({self.suite(trial) for trial in same_version}) > 1:
            extras.append(f"suite {suite[:8]}")
        return extras


def _ordered_keys(trials: Iterable[TrialResult]) -> list[tuple[str, str, str, str, int, int]]:
    """Comparability keys, newest task version first."""
    keys = sorted({trial.comparability_key() for trial in trials})
    return sorted(keys, key=lambda key: version_key(key[1]), reverse=True)


def sections(
    data: SiteData,
    trials: Sequence[TrialResult],
    leaderboards: Sequence[Leaderboard],
    *,
    community: bool = True,
    name_task: bool = False,
) -> list[Section]:
    """Verified sections first, then community ones; each holds one key, suite and axis set."""
    result: list[Section] = []
    for verified in (True, False) if community else (True,):
        pool = [trial for trial in trials if data.verified(trial) == verified]
        for key in _ordered_keys(pool):
            of_key = [trial for trial in pool if trial.comparability_key() == key]
            for suite in sorted({data.suite(trial) for trial in of_key}):
                of_suite = [trial for trial in of_key if data.suite(trial) == suite]
                fixed = dict(zip(_KEY_PARTS, (str(part) for part in key), strict=True))
                fixed["suite_sha256"] = suite
                extras = [f"version {key[1]}", *data.heading_extras(key, suite)]
                if name_task:
                    extras.insert(0, data.task_title(key[0]))
                if not verified:
                    extras.append("community results")
                for by, axes in leaderboards:
                    groups: dict[tuple[str, ...], list[TrialResult]] = {}
                    for trial in of_suite:
                        groups.setdefault(tuple(_AXES[a](trial) for a in axes), []).append(trial)
                    for values in sorted(groups):
                        per = dict(zip(axes, values, strict=True))
                        result.append(
                            _section(by, groups[values], {**fixed, **per}, extras, per, verified)
                        )
    return result


def _section(
    by: str,
    trials: list[TrialResult],
    fixed: dict[str, str],
    extras: list[str],
    per: dict[str, str],
    verified: bool,
) -> Section:
    refused: str | None = None
    rows: list[Row] = []
    try:
        rows = [to_row(row) for row in compare_trials(trials, ComparisonDimension(by))]
    except ValueError as exc:
        refused = str(exc)
    heading = " · ".join(
        [f"By {_BY_LABEL.get(by, by)}", *(f"{axis} {value}" for axis, value in per.items())]
        + extras
    )
    return Section(
        heading=heading, fixed=fixed, by=by, verified_only=verified, rows=rows, refused=refused
    )


def leaderboard_sections(data: SiteData, task_path: Callable[[str], str]) -> list[Section]:
    """``/leaderboard/``: harness + model rows, every task at its newest version, per stack."""
    verified = [trial for trial in data.trials if data.verified(trial)]
    newest: dict[str, str] = {}
    for trial in verified:
        current = newest.get(trial.task_id)
        if current is None or version_key(trial.task_version) > version_key(current):
            newest[trial.task_id] = trial.task_version
    # Every stack gets the same columns: each task at its newest verified
    # version. A task without runs on a stack leaves its rows incomplete there.
    tasks = [f"{task}@{version}" for task, version in sorted(newest.items())]
    result = []
    for stack in sorted({trial.environment.stack for trial in verified}):
        matrix = compare_matrix(
            verified,
            ComparisonDimension.HARNESS_MODEL,
            tasks,
            ResultFilter(stack=stack),
            suite_of=data.suite_by_run.get,
        )
        result.append(
            Section(
                heading=f"Harness + model across tasks · stack {stack}",
                fixed={"stack": stack},
                by="harness_model",
                verified_only=True,
                rows=[],
                refused=None,
                matrix=Matrix(
                    columns=[
                        MatrixColumn(
                            task=column.task,
                            task_version=column.task_version,
                            path=task_path(column.task),
                            refused=column.refused,
                        )
                        for column in matrix.columns
                    ],
                    rows=[
                        MatrixRow(
                            value=row.value,
                            cells=[to_row(cell) if cell else None for cell in row.cells],
                            overall=MatrixOverall(**asdict(row.overall)),
                        )
                        for row in matrix.rows
                    ],
                ),
            )
        )
    return result


def build_pages(data: SiteData) -> list[Page]:
    """Every page in the layout table; fails on a page path two values would share."""
    paths = PathRegistry()
    run_paths = {
        run_id: paths.claim(f"/runs/{slug(run_id)}/", f"run {run_id!r}")
        for run_id in (bundle.submission.run_id for bundle in data.bundles)
    }
    task_ids = sorted({trial.task_id for trial in data.trials})
    task_paths = {task: paths.claim(f"/tasks/{slug(task)}/", f"task {task!r}") for task in task_ids}
    fixed_pages = [
        ("/", "home"),
        ("/leaderboard/", "leaderboard"),
        ("/compare/", "compare"),
        ("/methodology/", "methodology"),
    ]
    for path, name in fixed_pages:
        paths.claim(path, name)

    task_links = [Link(path=task_paths[task], label=data.task_title(task)) for task in task_ids]
    latest = sorted(data.bundles, key=lambda b: (b.submission.submitted_at, b.submission.run_id))
    pages = [
        Page(
            path="/",
            kind="home",
            title="AI coding stack benchmark",
            description="How AI coding agents (harness + model) perform across programming "
            "languages and frameworks, from verified benchmark runs.",
            params={},
            sections=sections(
                data,
                [trial for trial in data.trials if data.verified(trial)],
                HOME,
                community=False,
                name_task=True,
            ),
            links=[
                *task_links,
                Link(path="/leaderboard/", label="Leaderboard"),
                Link(path="/compare/", label="Compare"),
                Link(path="/methodology/", label="Methodology"),
                *(
                    Link(path=run_paths[b.submission.run_id], label=f"Run {b.submission.run_id}")
                    for b in reversed(latest[-LATEST_RUNS:])
                ),
            ],
        ),
        Page(
            path="/leaderboard/",
            kind="leaderboard",
            title="Leaderboard",
            description="Harness + model results across every task (newest version each), "
            "one table per stack, verified runs only.",
            params={},
            sections=leaderboard_sections(data, task_paths.__getitem__),
            links=task_links,
        ),
        Page(
            path="/compare/",
            kind="compare",
            title="Compare results",
            description="Build a custom comparison of benchmark results in the browser.",
            params={},
            sections=[],
            links=[],
        ),
        Page(
            path="/methodology/",
            kind="methodology",
            title="Methodology",
            description="How trials run, what each metric means, which results are comparable, "
            "and verified versus community runs.",
            params={},
            sections=[],
            links=[],
        ),
    ]
    for task in task_ids:
        pages.extend(_task_pages(data, task, task_paths[task], paths, run_paths))
    for bundle in data.bundles:
        pages.append(_run_page(data, bundle, run_paths, task_paths))
    return pages


def _task_pages(
    data: SiteData, task: str, task_path: str, paths: PathRegistry, run_paths: dict[str, str]
) -> list[Page]:
    trials = [trial for trial in data.trials if trial.task_id == task]
    title = data.task_title(task)
    spec = data.catalog.get(task)
    kinds = (
        ("harness", "harnesses", "Harness", HARNESS, "by model, by stack and by harness version"),
        ("model", "models", "Model", MODEL, "by harness and by stack"),
        ("stack", "stacks", "Stack", STACK, "by harness + model"),
    )
    sub_pages: list[Page] = []
    links: list[Link] = []
    for axis, folder, label, leaderboards, compared in kinds:
        for value in sorted({_AXES[axis](trial) for trial in trials}):
            path = paths.claim(
                f"{task_path}{folder}/{slug(value)}/", f"{axis} {value!r} of task {task!r}"
            )
            links.append(Link(path=path, label=f"{label} {value}"))
            subset = [trial for trial in trials if _AXES[axis](trial) == value]
            sub_pages.append(
                Page(
                    path=path,
                    kind=axis,
                    title=f"{label} {value} on {title}",
                    description=f"Results of {axis} {value} on the {title} task, {compared}.",
                    params={"task": task, axis: value},
                    sections=sections(data, subset, leaderboards),
                    links=[Link(path=task_path, label=title)],
                )
            )
    runs = sorted({trial.run_id for trial in trials})
    links.extend(Link(path=run_paths[run], label=f"Run {run}") for run in runs)
    task_page = Page(
        path=task_path,
        kind="task",
        title=title,
        description=spec.description.strip() if spec else f"Benchmark results for task {task}.",
        params={"task": task},
        sections=sections(data, trials, TASK),
        links=links,
    )
    return [task_page, *sub_pages]


def _run_page(
    data: SiteData, bundle: Bundle, run_paths: dict[str, str], task_paths: dict[str, str]
) -> Page:
    info = bundle.submission
    return Page(
        path=run_paths[info.run_id],
        kind="run",
        title=f"Run {info.run_id}",
        description=f"Run {info.run_id} of {data.task_title(info.task_id)} "
        f"(version {info.task_version}): {len(bundle.trials)} trial(s), "
        + ("verified." if bundle.verified else "community result."),
        params={
            "run_id": info.run_id,
            "task": info.task_id,
            "task_version": info.task_version,
            "bundle": f"submissions/{info.task_id}/{info.run_id}/",
        },
        sections=[],
        links=[Link(path=task_paths[info.task_id], label=data.task_title(info.task_id))],
    )
