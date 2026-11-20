"""``aicsbench site export`` and ``compare_matrix`` (docs/phase-5/tasks/04-site-export.md)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from ai_benchmark.cli.app import app
from ai_benchmark.comparison import compare_matrix, compare_trials
from ai_benchmark.models import ComparisonDimension, TrialStatus
from ai_benchmark.models.results import TrialResult
from ai_benchmark.site.export import export_site
from ai_benchmark.site.slugs import SlugCollisionError
from ai_benchmark.storage import JsonResultStore
from ai_benchmark.storage.base import ResultFilter

from .regen_site_export import (
    BASE_URL,
    BUILT_AT,
    EXPORT,
    XSS,
    catalog,
    make_trial,
    regenerate,
    write_bundle,
    write_fixture_data,
)
from .test_storage import manifest

runner = CliRunner()


def load(out: Path, relative: str) -> object:
    return json.loads((out / relative).read_text(encoding="utf-8"))


def pages_of(out: Path) -> dict[str, dict]:
    return {page["path"]: page for page in load(out, "pages.json")["pages"]}  # type: ignore[index]


def run_export(data: Path, out: Path) -> None:
    export_site(data, out, BASE_URL, catalog(), built_at=BUILT_AT, platform_git_commit=None)


def trials_of(data: Path) -> list[TrialResult]:
    return [
        TrialResult.model_validate_json(file.read_bytes())
        for file in sorted(data.glob("submissions/*/*/trials/*.json"))
    ]


@pytest.fixture
def fixture_data(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    write_fixture_data(data)
    return data


# --- the committed fixture ----------------------------------------------------


def test_committed_fixture_matches_regeneration(tmp_path: Path) -> None:
    """``python -m tests.regen_site_export`` reproduces the committed export byte for byte."""
    out = tmp_path / "export"
    regenerate(tmp_path / "data", out)

    def files(root: Path) -> dict[str, bytes]:
        return {
            file.relative_to(root).as_posix(): file.read_bytes()
            for file in root.rglob("*")
            if file.is_file()
        }

    assert files(out) == files(EXPORT)


def test_fixture_validates_against_its_own_schemas() -> None:
    for name, relative in (
        ("export", "export.json"),
        ("trials", "data/trials.json"),
        ("meta", "data/meta.json"),
        ("pages", "pages.json"),
    ):
        schema = load(EXPORT, f"schema/{name}.schema.json")
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(load(EXPORT, relative), schema, cls=jsonschema.Draft202012Validator)
    broken = load(EXPORT, "data/trials.json")
    broken[0]["unknown"] = 1  # type: ignore[index]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(broken, load(EXPORT, "schema/trials.schema.json"))


def test_fixture_lists_every_page_kind_with_known_values() -> None:
    pages = pages_of(EXPORT)
    assert {page["kind"] for page in pages.values()} == {
        "home",
        "task",
        "harness",
        "model",
        "stack",
        "leaderboard",
        "run",
        "compare",
        "methodology",
    }
    assert all(path.endswith("/") for path in pages)
    task = pages["/tasks/task-management-api/"]
    assert task["title"] == "Task Management REST API"
    verified, *_, community = task["sections"]
    assert verified["verified_only"] is True
    assert verified["fixed"]["stack"] == "fastapi"
    assert verified["rows"][0]["value"] == "codex + gpt-6-luna"
    assert verified["rows"][0]["success_rate"] == pytest.approx(2 / 3)
    assert community["verified_only"] is False
    assert "community results" in community["heading"]
    assert "/tasks/task-management-api/harnesses/claude/" in pages
    assert pages["/runs/run-verified-001/"]["params"]["run_id"] == "run-verified-001"
    leaderboard = pages["/leaderboard/"]["sections"]
    assert [section["fixed"] for section in leaderboard] == [{"stack": "fastapi"}]
    assert all(section["verified_only"] for section in pages["/"]["sections"])


def test_fixture_copies_submitted_strings_unchanged() -> None:
    rows = load(EXPORT, "data/trials.json")
    community = [row for row in rows if row["run_id"] == "run-community-001"]  # type: ignore[union-attr]
    assert {row["submitter"] for row in community} == {XSS}
    assert {row["verified"] for row in community} == {False}
    submission = load(EXPORT, "runs/run-community-001/submission.json")
    assert submission["submitter"]["name"] == XSS  # type: ignore[index]
    assert XSS in submission["notes"]  # type: ignore[index]
    verified = [row for row in rows if row["run_id"] == "run-verified-001"]  # type: ignore[union-attr]
    assert {row["submitter"] for row in verified} == {"verified-submitter"}
    meta = load(EXPORT, "data/meta.json")
    assert (meta["runs"], meta["trials"]) == (2, 5)  # type: ignore[index]


def test_unavailable_means_are_null_not_zero() -> None:
    pages = pages_of(EXPORT)
    community = pages["/tasks/task-management-api/stacks/laravel/"]["sections"][0]
    row = community["rows"][0]
    assert (row["passed"], row["mean_seconds"], row["mean_cost_usd"]) == (0, None, None)
    assert row["success_rate"] == 0


def test_export_accepts_task_9_trial_row_keys(
    fixture_data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``trial_row`` gains provider and endpoint_host with task 9; the export must keep up."""
    import ai_benchmark.site.export as export_module

    original = export_module.trial_row

    def with_provider(trial: TrialResult) -> dict[str, object]:
        return {**original(trial), "provider": "openai", "endpoint_host": "custom"}

    monkeypatch.setattr(export_module, "trial_row", with_provider)
    out = tmp_path / "out"
    run_export(fixture_data, out)
    rows = load(out, "data/trials.json")
    assert {(row["provider"], row["endpoint_host"]) for row in rows} == {  # type: ignore[union-attr]
        ("openai", "custom")
    }
    jsonschema.validate(rows, load(out, "schema/trials.schema.json"))


# --- leaderboards are compare_trials ------------------------------------------


def test_every_section_equals_compare_trials(fixture_data: Path, tmp_path: Path) -> None:
    # Add a second, unverified version and suite, so the split has work to do.
    write_bundle(
        fixture_data,
        [make_trial("run-old-001", 1, version="1", harness="codex", seconds=300.0)],
        suite="1" * 64,
    )
    out = tmp_path / "out"
    run_export(fixture_data, out)
    trials = trials_of(fixture_data)
    suites = {
        row["run_id"]: row["suite_sha256"]
        for row in load(out, "data/trials.json")  # type: ignore[union-attr]
    }
    verified = {
        row["run_id"]: row["verified"]
        for row in load(out, "data/trials.json")  # type: ignore[union-attr]
    }
    axes = {
        "stack": lambda t: t.environment.stack,
        "harness": lambda t: t.agent.name,
        "model": lambda t: t.agent.model_key,
        "task": lambda t: t.task_id,
        "task_version": lambda t: t.task_version,
        "suite_sha256": lambda t: suites[t.run_id],
    }
    checked = 0
    for page in pages_of(out).values():
        for section in page["sections"]:
            if section.get("matrix"):
                continue
            selected = [
                trial
                for trial in trials
                if verified[trial.run_id] == section["verified_only"]
                and all(
                    str(axes[key](trial)) == value
                    for key, value in {**page["params"], **section["fixed"]}.items()
                    if key in axes
                )
                and str(trial.comparability_key()[2:])
                == str(
                    tuple(
                        section["fixed"][k]
                        if k in ("tooling_mode", "auth_mode")
                        else int(section["fixed"][k])
                        for k in ("tooling_mode", "auth_mode", "max_repairs", "max_parallel")
                    )
                )
            ]
            expected = compare_trials(selected, ComparisonDimension(section["by"]))
            assert [
                {k: v for k, v in row.items() if k != "success_rate"} for row in section["rows"]
            ] == [asdict(row) for row in expected]
            checked += 1
    assert checked > 10


def test_suite_hashes_and_old_versions_get_their_own_sections(
    fixture_data: Path, tmp_path: Path
) -> None:
    write_bundle(
        fixture_data,
        [make_trial("run-suite-b", 1, seconds=100.0)],
        suite="b" * 64,
        verified=True,
    )
    write_bundle(
        fixture_data,
        [make_trial("run-v1", 1, version="1", seconds=50.0)],
        suite="c" * 64,
        verified=True,
    )
    out = tmp_path / "out"
    run_export(fixture_data, out)
    task = pages_of(out)["/tasks/task-management-api/"]
    by_model = [s for s in task["sections"] if s["by"] == "harness_model" and s["verified_only"]]
    assert [(s["fixed"]["task_version"], s["fixed"]["suite_sha256"][:8]) for s in by_model] == [
        ("2", "bbbbbbbb"),
        ("2", "bdb9517a"),
        ("1", "cccccccc"),
    ]
    assert "suite bbbbbbbb" in by_model[0]["heading"]
    assert "suite" not in by_model[2]["heading"]  # version 1 has a single suite
    assert [row["trials"] for s in by_model for row in s["rows"]] == [1, 3, 1]
    meta = load(out, "data/meta.json")
    assert {(t["id"], t["version"]) for t in meta["tasks"]} == {  # type: ignore[index]
        ("task-management-api", "1"),
        ("task-management-api", "2"),
    }
    # The leaderboard uses the newest version only and refuses mixed suites there.
    column = pages_of(out)["/leaderboard/"]["sections"][0]["matrix"]["columns"][0]
    assert column["task_version"] == "2"
    assert column["refused"] == "comparison mixes acceptance suites; pick one suite"


def test_task_missing_from_catalog_is_listed_by_id(fixture_data: Path, tmp_path: Path) -> None:
    write_bundle(fixture_data, [make_trial("run-gone", 1, task="retired-task")], verified=True)
    out = tmp_path / "out"
    run_export(fixture_data, out)
    page = pages_of(out)["/tasks/retired-task/"]
    assert page["title"] == "retired-task"
    assert {"id": "retired-task", "version": "2", "name": None, "description": None} in load(
        out, "data/meta.json"
    )["tasks"]  # type: ignore[index]


# --- skipping, slugs and data_commit ------------------------------------------


def test_invalid_bundle_is_skipped_with_a_warning(fixture_data: Path, tmp_path: Path) -> None:
    broken = write_bundle(fixture_data, [make_trial("run-broken", 1)], verified=True)
    (broken / "trials" / "trial-001.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["site", "export", "--data", str(fixture_data), "--out", str(out), "--base-url", BASE_URL],
    )
    assert result.exit_code == 0, result.output
    assert "skipped invalid bundle" in result.output and "run-broken" in result.output
    assert "/runs/run-broken/" not in pages_of(out)
    assert not (out / "runs" / "run-broken").exists()
    assert (out / "runs" / "run-verified-001" / "manifest.json").is_file()


def test_colliding_base_slugs_get_distinct_stable_paths(tmp_path: Path) -> None:
    def stack_paths(stacks: list[str]) -> set[str]:
        data = tmp_path / "-".join(str(len(s)) for s in stacks) / str(len(stacks))
        data.mkdir(parents=True)
        for index, stack in enumerate(stacks):
            write_bundle(data, [make_trial(f"run-{index}", 1, stack=stack)], verified=True)
        out = data.parent / "out"
        run_export(data, out)
        return {path for path in pages_of(out) if "/stacks/" in path}

    alone = stack_paths(["Foo/Bar"])
    assert alone == {"/tasks/task-management-api/stacks/foo-bar-c84b3c5b/"}
    both = stack_paths(["Foo/Bar", "Foo-Bar"])
    assert alone < both and len(both) == 2
    literal = stack_paths(["Foo/Bar", "foo-bar-c84b3c5b"])
    assert len(literal) == 2 and alone < literal


def test_colliding_suffixed_slugs_fail_and_write_nothing(tmp_path: Path) -> None:
    data = tmp_path / "data"
    for index, stack in enumerate(["ABcdEfgHijkLMNopqrs", "ABCDeFghijkLMnOPQrs"]):
        write_bundle(data, [make_trial(f"run-{index}", 1, stack=stack)])
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("previous export")
    with pytest.raises(SlugCollisionError) as error:
        run_export(data, out)
    assert "abcdefghijklmnopqrs-577c43dd" in str(error.value)
    assert "ABcdEfgHijkLMNopqrs" in str(error.value)
    assert "ABCDeFghijkLMnOPQrs" in str(error.value)
    assert [file.name for file in out.iterdir()] == ["keep.txt"]


def test_out_may_not_be_a_symlinked_submissions_target(tmp_path: Path) -> None:
    from ai_benchmark.site.export import SiteExportError

    real = tmp_path / "data-real"
    write_fixture_data(real)
    data = tmp_path / "data"
    data.mkdir()
    (data / "submissions").symlink_to(real / "submissions", target_is_directory=True)
    for out in (data / "submissions", real / "submissions", real):
        with pytest.raises(SiteExportError):
            run_export(data, out)
    assert len(trials_of(real)) == 5


@pytest.mark.parametrize("relative", ["submissions", "submissions/out", "."])
def test_out_may_not_hold_the_bundles(fixture_data: Path, relative: str) -> None:
    from ai_benchmark.site.export import SiteExportError

    with pytest.raises(SiteExportError):
        run_export(fixture_data, fixture_data / relative)
    assert len(trials_of(fixture_data)) == 5


def test_leaderboard_columns_are_the_same_on_every_stack(tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_bundle(data, [make_trial("run-a", 1, stack="fastapi")], verified=True)
    write_bundle(
        data,
        [make_trial("run-b", 1, task="url-shortener-api", version="1", stack="laravel")],
        verified=True,
    )
    out = tmp_path / "out"
    run_export(data, out)
    sections = pages_of(out)["/leaderboard/"]["sections"]
    for section in sections:
        matrix = section["matrix"]
        assert [(c["task"], c["task_version"]) for c in matrix["columns"]] == [
            ("task-management-api", "2"),
            ("url-shortener-api", "1"),
        ]
        assert [(r["overall"]["covered"], r["overall"]["complete"]) for r in matrix["rows"]] == [
            (1, False)
        ]


def test_data_commit_only_for_a_repository_root(fixture_data: Path, tmp_path: Path) -> None:
    # The committed fixture covers a plain folder inside this repository.
    assert load(EXPORT, "export.json")["data_commit"] is None  # type: ignore[index]
    git = ["git", "-C", str(fixture_data), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "data"], check=True)
    head = subprocess.run(
        [*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    run_export(fixture_data, tmp_path / "out")
    assert load(tmp_path / "out", "export.json")["data_commit"] == head  # type: ignore[index]


# --- compare_matrix -----------------------------------------------------------


def _matrix_trials() -> list[TrialResult]:
    def t(task: str, index: int, harness: str, model: str, **kw: object) -> TrialResult:
        return make_trial(f"r-{task}", index, task=task, harness=harness, model=model, **kw)  # type: ignore[arg-type]

    return [
        # Task a: Y is faster and better than X.
        t("a", 1, "codex", "x", seconds=500.0),
        t("a", 2, "codex", "x", status=TrialStatus.FAILED),
        t("a", 3, "claude", "y", seconds=100.0),
        # Task b: X only, so Y is incomplete.
        t("b", 4, "codex", "x", seconds=900.0),
        # Task c mixes stacks for one harness + model: refused.
        t("c", 5, "codex", "x"),
        t("c", 6, "codex", "x", stack="laravel"),
    ]


def test_compare_matrix_columns_equal_compare_trials_and_rank_incomplete_last() -> None:
    trials = _matrix_trials()
    by = ComparisonDimension.HARNESS_MODEL
    matrix = compare_matrix(trials, by, ["a", "b", "c"])
    for column in matrix.columns[:2]:
        assert list(column.rows) == compare_trials(
            [trial for trial in trials if trial.task_id == column.task], by
        )
    refused = matrix.columns[2]
    assert refused.rows == ()
    with pytest.raises(ValueError) as error:
        compare_trials([trial for trial in trials if trial.task_id == "c"], by)
    assert refused.refused == str(error.value)

    x, y = matrix.rows
    assert (x.value, y.value) == ("codex + x", "claude + y")
    assert (x.overall.complete, x.overall.covered, x.overall.columns) == (True, 2, 2)
    assert x.overall.success_rate == pytest.approx(0.75)
    assert x.overall.mean_rank == pytest.approx(1.5)
    assert x.cells[2] is None
    assert (y.overall.complete, y.overall.covered, y.overall.success_rate) == (False, 1, 1.0)
    assert y.cells[1] is None


def test_compare_matrix_counts_each_task_version_once() -> None:
    trials = _matrix_trials()
    once = compare_matrix(trials, ComparisonDimension.HARNESS_MODEL, ["a", "b"])
    twice = compare_matrix(trials, ComparisonDimension.HARNESS_MODEL, ["a", "a@2", "b", "a"])
    assert twice == once


def test_compare_matrix_applies_filters_and_pins_versions() -> None:
    trials = [
        *_matrix_trials(),
        make_trial("r-a3", 9, task="a", version="3", harness="codex", model="x"),
    ]
    newest = compare_matrix(trials, ComparisonDimension.HARNESS_MODEL, ["a"])
    assert newest.columns[0].task_version == "3"
    pinned = compare_matrix(
        trials, ComparisonDimension.HARNESS_MODEL, ["a@2"], ResultFilter(agent="claude")
    )
    assert pinned.columns[0].task_version == "2"
    assert [row.value for row in pinned.rows] == ["claude + y"]


def test_cli_compare_tasks_prints_matrix(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path / "results")
    for trial in _matrix_trials():
        store.save_manifest(manifest(trial.run_id))
        store.save_trial(trial)
    base = ["--results-dir", str(tmp_path / "results"), "compare", "--by", "harness_model"]
    result = runner.invoke(app, [*base, "--matrix", "a,b,c"], terminal_width=250)
    assert result.exit_code == 0, result.output
    assert "incomplete" in result.output and "refused" in result.output
    other_suite = make_trial("r-a-other", 7, task="a", harness="codex", model="x")
    other = manifest(other_suite.run_id).model_copy(update={"acceptance_suite_sha256": "e" * 64})
    store.save_manifest(other)
    store.save_trial(other_suite)
    unrelated = make_trial("r-unrelated", 8, task="z")
    store.save_manifest(manifest(unrelated.run_id))
    store.save_trial(unrelated)
    (tmp_path / "results" / "r-unrelated" / "manifest.json").write_text("{broken")
    old_version = make_trial("r-a-old", 9, task="a", version="1")
    store.save_manifest(manifest(old_version.run_id))
    store.save_trial(old_version)
    (tmp_path / "results" / "r-a-old" / "manifest.json").write_text("{broken")
    mixed = runner.invoke(app, [*base, "--matrix", "a,b"], terminal_width=250)
    assert mixed.exit_code == 0, mixed.output
    assert "acceptance" in mixed.output and "refused" in mixed.output
    single = runner.invoke(app, [*base, "--tasks", "b"])
    plain = runner.invoke(app, [*base, "--task", "b"])
    assert single.exit_code == 0 and single.output == plain.output
