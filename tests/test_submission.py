"""Submission bundle validation (docs/phase-5/tasks/02-bundle-and-validator.md)."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_benchmark.cli.app import app
from ai_benchmark.config.loader import SpecCatalog
from ai_benchmark.models.results import RunManifest
from ai_benchmark.submission.secrets import find_secrets, scan_text
from ai_benchmark.submission.suite import EMPTY_SHA256, suite_sha256
from ai_benchmark.submission.validate import validate_bundle

from .conftest import REPO_ROOT

FIXTURE_BUNDLE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "submissions"
    / "submissions"
    / "task-management-api"
    / "run-fixture-001"
)
TINY_SUITE_SHA256 = "246a89cb55c1a46e7232604520223dedbe07c07299e3aae0464081c8bc20daed"
DIRS = [
    "--tasks-dir",
    str(REPO_ROOT / "benchmarks" / "tasks"),
    "--stacks-dir",
    str(REPO_ROOT / "benchmarks" / "stacks"),
]
runner = CliRunner()


@pytest.fixture
def catalog(settings) -> SpecCatalog:
    return SpecCatalog(settings)


def copy_bundle(tmp_path: Path) -> Path:
    """A pristine copy of the valid fixture under tmp_path."""
    target = tmp_path / "data" / "submissions" / "task-management-api" / "run-fixture-001"
    shutil.copytree(FIXTURE_BUNDLE, target)
    return target


def read_json(bundle: Path, relative: str) -> dict:
    return json.loads((bundle / relative).read_text(encoding="utf-8"))


def write_json(bundle: Path, relative: str, data: dict) -> None:
    (bundle / relative).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def refresh_hashes(bundle: Path, new_files: list[str] | None = None) -> None:
    """Recompute submission.json's files dict after a bundle file changed."""
    submission = read_json(bundle, "submission.json")
    files = submission["files"]
    for relative in new_files or list(files):
        files[relative] = hashlib.sha256((bundle / relative).read_bytes()).hexdigest()
    write_json(bundle, "submission.json", submission)


def codes(problems) -> list[str]:
    return [problem.code for problem in problems]


# --- the valid fixture ------------------------------------------------------


def test_valid_fixture_passes(catalog: SpecCatalog) -> None:
    assert validate_bundle(FIXTURE_BUNDLE, catalog) == []


def test_valid_fixture_copy_passes(catalog: SpecCatalog, tmp_path: Path) -> None:
    assert validate_bundle(copy_bundle(tmp_path), catalog) == []


# --- one test per problem code -----------------------------------------------


def test_layout_rejects_unexpected_file(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    (bundle / "README.txt").write_text("hi")
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems if p.code == "layout"] == ["layout"]


def test_layout_rejects_missing_required_file(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    (bundle / "manifest.json").unlink()
    assert "layout" in codes(validate_bundle(bundle, catalog))


def test_layout_rejects_symlinked_trials_dir(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    real = tmp_path / "real-trials"
    shutil.move(bundle / "trials", real)
    (bundle / "trials").symlink_to(real, target_is_directory=True)
    problems = validate_bundle(bundle, catalog)
    assert "layout" in codes(problems)
    assert "symlink" in problems[0].message


def test_layout_requires_log_archive_when_logs_included(
    catalog: SpecCatalog, tmp_path: Path
) -> None:
    bundle = _log_bundle(catalog, tmp_path)
    (bundle / "logs").mkdir()
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["layout"]
    assert "no logs/*.tar.gz" in problems[0].message


def test_layout_rejects_log_archive_outside_trial_ids(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = _log_bundle(catalog, tmp_path)
    _add_log(bundle, "trial-999", "harmless")
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["layout"]
    assert "logs/trial-999.tar.gz" in problems[0].message


def test_schema_rejects_broken_json(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    (bundle / "submission.json").write_text("{not json", encoding="utf-8")
    problems = validate_bundle(bundle, catalog)
    assert "schema" in codes(problems)


def test_schema_rejects_extra_field(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "trials/trial-001.json")
    data["unexpected"] = 1
    write_json(bundle, "trials/trial-001.json", data)
    refresh_hashes(bundle, ["trials/trial-001.json"])
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["schema"]
    assert "trials/trial-001.json" in problems[0].path


def test_schema_rejects_invalid_utf8(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    (bundle / "manifest.json").write_bytes(b'{"run_id": "\xff\xfe"}')
    problems = validate_bundle(bundle, catalog)
    assert "schema" in codes(problems)


def test_schema_rejects_deeply_nested_json(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    (bundle / "trials" / "trial-001.json").write_text("[" * 10000 + "]" * 10000)
    problems = validate_bundle(bundle, catalog)
    assert "schema" in codes(problems)


def test_schema_rejects_oversized_json_integer(catalog: SpecCatalog, tmp_path: Path) -> None:
    # On Python 3.11+ json.loads raises plain ValueError for integers over
    # 4300 digits, which JSONDecodeError handlers do not cover.
    bundle = copy_bundle(tmp_path)
    (bundle / "manifest.json").write_text('{"run_id": "run-fixture-001", "n": ' + "9" * 5000 + "}")
    problems = validate_bundle(bundle, catalog)
    assert "schema" in codes(problems)


@pytest.mark.parametrize(
    "submitted_at",
    ["2026-11-17T08:00:00+02:00", "2026-11-17T08:00:00"],
)
def test_schema_rejects_non_utc_submitted_at(
    catalog: SpecCatalog, tmp_path: Path, submitted_at: str
) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "submission.json")
    data["submitted_at"] = submitted_at
    write_json(bundle, "submission.json", data)
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["schema"]


def test_schema_rejects_github_with_at_sign(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "submission.json")
    data["submitter"]["github"] = "@alice"
    write_json(bundle, "submission.json", data)
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["schema"]


def test_hash_rejects_tampered_file(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "trials/trial-001.json")
    data["error"] = "tampered"
    write_json(bundle, "trials/trial-001.json", data)
    assert "hash" in codes(validate_bundle(bundle, catalog))


def test_hash_rejects_unlisted_file(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    (bundle / "trials" / "trial-003.json").write_text("{}", encoding="utf-8")
    assert "hash" in codes(validate_bundle(bundle, catalog))


def test_hash_rejects_missing_file(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    (bundle / "trials" / "trial-002.json").unlink()
    assert "hash" in codes(validate_bundle(bundle, catalog))


@pytest.mark.parametrize(
    "entry", ["../escape.txt", "/etc/aicsbench-escape", "trials/../manifest.json"]
)
def test_hash_rejects_escaping_files_entry(
    catalog: SpecCatalog, tmp_path: Path, entry: str
) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "submission.json")
    data["files"][entry] = "0" * 64
    write_json(bundle, "submission.json", data)
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["hash"]
    assert problems[0].path == entry
    assert "escape" in problems[0].message


def test_hash_rejects_unreadable_file(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    locked = bundle / "trials" / "trial-001.json"
    locked.chmod(0)
    try:
        problems = validate_bundle(bundle, catalog)
    finally:
        locked.chmod(0o644)
    assert "hash" in codes(problems)
    assert any("cannot read" in p.message for p in problems if p.code == "hash")


def test_hash_rejects_unresolvable_symlink(catalog: SpecCatalog, tmp_path: Path) -> None:
    # A self-referencing symlink: Path.resolve raises RuntimeError on Python
    # 3.10-3.12, while 3.13+ returns the link itself, so the entry then reads
    # as missing. Either way the entry must be reported, never crash.
    bundle = copy_bundle(tmp_path)
    loop = bundle / "trials" / "loop.json"
    loop.symlink_to(loop)
    data = read_json(bundle, "submission.json")
    data["files"]["trials/loop.json"] = "0" * 64
    write_json(bundle, "submission.json", data)
    refresh_hashes(bundle, ["submission.json"])
    problems = validate_bundle(bundle, catalog)
    assert "layout" in codes(problems)  # the symlink itself is still rejected
    assert "trials/loop.json" in {p.path for p in problems if p.code == "hash"}


def test_ids_reject_mismatched_run_id(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "manifest.json")
    data["run_id"] = "run-other-002"
    write_json(bundle, "manifest.json", data)
    refresh_hashes(bundle, ["manifest.json"])
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["ids"]


def test_ids_reject_mismatched_folder_name(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    renamed = bundle.with_name("run-renamed-001")
    bundle.rename(renamed)
    assert "ids" in codes(validate_bundle(renamed, catalog))


def test_ids_reject_duplicate_trial_id(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "trials/trial-001.json")
    write_json(bundle, "trials/trial-003.json", data)
    refresh_hashes(bundle, ["trials/trial-003.json"])
    problems = validate_bundle(bundle, catalog)
    assert set(codes(problems)) == {"ids"}
    assert any("duplicate trial_id" in p.message for p in problems)


def test_ids_reject_trial_id_that_differs_from_file_name(
    catalog: SpecCatalog, tmp_path: Path
) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "trials/trial-001.json")
    data["trial_id"] = "trial-999"
    write_json(bundle, "trials/trial-001.json", data)
    refresh_hashes(bundle, ["trials/trial-001.json"])
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["ids"]
    assert problems[0].path == "trials/trial-001.json"


def test_ids_reject_trial_run_id_mismatch(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "trials/trial-001.json")
    data["run_id"] = "run-other-001"
    write_json(bundle, "trials/trial-001.json", data)
    refresh_hashes(bundle, ["trials/trial-001.json"])
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["ids"]
    assert "run_id disagrees" in problems[0].message


def test_ids_reject_platform_mismatch(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "submission.json")
    data["platform_version"] = "9.9.9"
    data["platform_git_commit"] = "0000000"
    write_json(bundle, "submission.json", data)
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["ids"]
    assert "platform_version disagrees" in problems[0].message
    assert "platform_git_commit disagrees" in problems[0].message


def test_task_rejects_unknown_task(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    submission = read_json(bundle, "submission.json")
    submission["task_id"] = "does-not-exist"
    write_json(bundle, "submission.json", submission)
    problems = validate_bundle(bundle, catalog)
    assert "task" in codes(problems)


def test_task_rejects_unknown_version(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    submission = read_json(bundle, "submission.json")
    submission["task_version"] = "999"
    write_json(bundle, "submission.json", submission)
    assert "task" in codes(validate_bundle(bundle, catalog))


def test_suite_rejects_hash_that_differs_from_manifest(
    catalog: SpecCatalog, tmp_path: Path
) -> None:
    bundle = copy_bundle(tmp_path)
    submission = read_json(bundle, "submission.json")
    submission["acceptance_suite_sha256"] = "0" * 64
    write_json(bundle, "submission.json", submission)
    problems = validate_bundle(bundle, catalog, check_catalog=False)
    assert [p.code for p in problems] == ["suite"]


def test_suite_rejects_hash_that_differs_from_catalog(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    submission = read_json(bundle, "submission.json")
    submission["acceptance_suite_sha256"] = "0" * 64
    submission["suite_hash_source"] = "submit"
    write_json(bundle, "submission.json", submission)
    assert "suite" in codes(validate_bundle(bundle, catalog))


def test_suite_rejects_manifest_hash_mismatch_for_submit_source(
    catalog: SpecCatalog, tmp_path: Path
) -> None:
    """A recorded manifest hash must match whatever the source of the submission's hash."""
    bundle = copy_bundle(tmp_path)
    submission = read_json(bundle, "submission.json")
    submission["acceptance_suite_sha256"] = "0" * 64
    submission["suite_hash_source"] = "submit"
    write_json(bundle, "submission.json", submission)
    problems = validate_bundle(bundle, catalog, check_catalog=False)
    assert [p.code for p in problems] == ["suite"]
    assert "manifest.json" in problems[0].path


def test_rebuild_mode_passes_unknown_task_version(catalog: SpecCatalog, tmp_path: Path) -> None:
    """Merged bundles for a dropped task version stay valid in rebuild mode."""
    bundle = copy_bundle(tmp_path)
    for relative in (
        "submission.json",
        "manifest.json",
        "trials/trial-001.json",
        "trials/trial-002.json",
    ):
        data = read_json(bundle, relative)
        if "task_version" in data:
            data["task_version"] = "999"
        else:
            data["task_snapshot"]["version"] = "999"
        write_json(bundle, relative, data)
    refresh_hashes(bundle, ["manifest.json", "trials/trial-001.json", "trials/trial-002.json"])
    assert validate_bundle(bundle, catalog, check_catalog=False) == []


def test_comparable_rejects_mixed_keys(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "trials/trial-002.json")
    data["max_repairs"] = 1
    write_json(bundle, "trials/trial-002.json", data)
    refresh_hashes(bundle, ["trials/trial-002.json"])
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["comparable"]


def test_harness_rejects_missing_version(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "trials/trial-001.json")
    data["agent"]["version"] = None
    write_json(bundle, "trials/trial-001.json", data)
    refresh_hashes(bundle, ["trials/trial-001.json"])
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["harness"]
    assert "trials/trial-001.json" in problems[0].path


def test_status_rejects_unfinished_trial(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "trials/trial-001.json")
    data["status"] = "running"
    write_json(bundle, "trials/trial-001.json", data)
    refresh_hashes(bundle, ["trials/trial-001.json"])
    assert codes(validate_bundle(bundle, catalog)) == ["status"]


def test_secret_rejects_planted_token(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "trials/trial-001.json")
    data["raw"] = {"access_token": "super-secret-value-123"}
    write_json(bundle, "trials/trial-001.json", data)
    refresh_hashes(bundle, ["trials/trial-001.json"])
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["secret"]
    assert "raw.access_token" in problems[0].message


def test_duplicate_rejects_same_run_id(catalog: SpecCatalog, tmp_path: Path) -> None:
    against = tmp_path / "base"
    copied = copy_bundle(tmp_path)
    duplicate = against / "submissions" / "task-management-api" / "run-fixture-001"
    duplicate.parent.mkdir(parents=True)
    shutil.copytree(FIXTURE_BUNDLE, duplicate)
    problems = validate_bundle(copied, catalog, against=against)
    assert [p.code for p in problems] == ["duplicate"]


def test_duplicate_accepts_itself(catalog: SpecCatalog, tmp_path: Path) -> None:
    """The bundle being validated is never its own duplicate, even a copy of it is."""
    bundle = copy_bundle(tmp_path)
    against = bundle.parent.parent.parent
    other = against / "other" / "run-fixture-001"
    other.parent.mkdir(parents=True)
    shutil.copytree(FIXTURE_BUNDLE, other)
    problems = validate_bundle(bundle, catalog, against=against)
    assert [p.code for p in problems] == ["duplicate"]
    assert problems[0].message == "same run_id as other/run-fixture-001"


def test_duplicate_allows_relative_again_dir(
    catalog: SpecCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = copy_bundle(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert validate_bundle(bundle, catalog, against=Path("data")) == []


def test_duplicate_reports_unreadable_manifest(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    locked = bundle / "manifest.json"
    locked.chmod(0)
    try:
        problems = validate_bundle(bundle, catalog, against=tmp_path / "data")
    finally:
        locked.chmod(0o644)
    assert "layout" in codes(problems)
    assert any("cannot read manifest.json" in p.message for p in problems if p.code == "layout")


# --- suite hash --------------------------------------------------------------


def test_suite_sha256_is_stable_and_skips_pycache(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    (suite / "b").mkdir(parents=True)
    (suite / "__pycache__").mkdir()
    (suite / "a.txt").write_bytes(b"hello\n")
    (suite / "b" / "c.txt").write_bytes(b"world\n")
    (suite / "__pycache__" / "x.pyc").write_bytes(b"junk")
    assert suite_sha256(suite) == TINY_SUITE_SHA256


def test_suite_sha256_of_empty_task() -> None:
    assert suite_sha256(_suiteless_task()) == EMPTY_SHA256


def test_suite_sha256_fails_on_unreadable_directory(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    locked = suite / "locked"
    locked.mkdir(parents=True)
    (suite / "a.txt").write_bytes(b"a\n")
    locked.chmod(0)
    try:
        with pytest.raises(OSError):
            suite_sha256(suite)
    finally:
        locked.chmod(0o755)


def test_suite_sha256_rejects_symlinked_directory(tmp_path: Path) -> None:
    cases = tmp_path / "cases-a"
    cases.mkdir()
    (cases / "test_x.txt").write_bytes(b"a\n")
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "tests").symlink_to(cases, target_is_directory=True)
    with pytest.raises(OSError):
        suite_sha256(suite)


def test_shipped_suite_hashes_are_unchanged(catalog: SpecCatalog) -> None:
    recorded = json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "suite-hashes.json").read_text(encoding="utf-8")
    )
    for task_id, spec in catalog.tasks().items():
        assert suite_sha256(spec) == recorded[f"{task_id}@{spec.version}"], (
            f"suite of {task_id}@{spec.version} changed; ship it as a new task version "
            "and regenerate tests/fixtures/suite-hashes.json"
        )


def _suiteless_task():
    from ai_benchmark.config.task import BenchmarkSpec

    return BenchmarkSpec.model_validate(
        {
            "id": "suiteless",
            "name": "Suiteless",
            "description": "no acceptance suite",
            "functional_requirements": ["do the thing"],
            "prompt_template": "go",
            "supported_stacks": ["fastapi"],
            "acceptance_tests": {},
        }
    )


# --- secret scanning ---------------------------------------------------------


@pytest.mark.parametrize(
    "pattern_secret",
    [
        "sk-" + "x" * 24,  # provider key
        "sk-ant-" + "x" * 24,  # anthropic key
        "ghp_" + "a" * 36,  # github token
        "xoxb-" + "a" * 12,  # slack token
        "-----BEGIN RSA PRIVATE KEY-----",
        "eyJ" + "a" * 22 + "." + "b" * 22 + ".",  # JWT
        "OPENAI_API_KEY=" + "v" * 20,
        "AKIAIOSFODNN7EXAMPLE",  # aws access key id
        "AWS_SECRET_ACCESS_KEY=" + "w" * 20,
        "MAX_PASSWORD=hunter2secret",
    ],
)
def test_find_secrets_flags_every_pattern(pattern_secret: str) -> None:
    assert find_secrets({"value": pattern_secret})


def test_find_secrets_flags_secret_keys_with_values() -> None:
    assert find_secrets({"access_token": "abc"}) == ["access_token"]
    assert find_secrets({"ACCESS_TOKEN": "abc"}) == ["ACCESS_TOKEN"]
    assert find_secrets({"password": "hunter2"}) == ["password"]  # not an assignment pattern
    assert find_secrets({"SECRET": False}) == []  # non-string value is fine
    assert find_secrets({"db": {"password": ""}}) == []  # empty value is fine


def test_find_secrets_redacts_secret_named_keys() -> None:
    key = "sk-" + "x" * 24
    assert find_secrets({key: "value"}) == ["<secret-key>"]
    assert find_secrets({"raw": {key: "v"}}) == ["raw.<secret-key>"]


def test_find_secrets_survives_deep_nesting() -> None:
    deep: object = {"access_token": "abc"}
    for _ in range(1000):
        deep = {"nested": deep}
    paths = find_secrets(deep)
    assert len(paths) == 1
    assert paths[0].endswith("access_token")


def test_scan_text_flags_jsonl_and_env_lines() -> None:
    assert scan_text('{"access_token": "' + "t" * 16 + '"}')
    assert scan_text("AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE")
    assert scan_text('2026-11-17 auth {"apikey": "abcd1234"}')
    assert scan_text("nothing secret here") == []
    assert scan_text('"secret": false') == []


def test_scan_text_catches_structured_secret_across_lines() -> None:
    assert scan_text('{"access_token":\n"abcd1234"}') == ["line 1"]


def test_secret_scan_has_no_false_positive_on_valid_fixture(catalog: SpecCatalog) -> None:
    for file in FIXTURE_BUNDLE.rglob("*.json"):
        assert find_secrets(json.loads(file.read_text(encoding="utf-8"))) == []


def _log_bundle(catalog: SpecCatalog, tmp_path: Path, logs_included: bool = True) -> Path:
    """A valid fixture copy with logs_included set, ready to receive a log archive."""
    bundle = copy_bundle(tmp_path)
    submission = read_json(bundle, "submission.json")
    submission["logs_included"] = logs_included
    write_json(bundle, "submission.json", submission)
    refresh_hashes(bundle)  # only submission.json changed; it is not in files
    return bundle


def _add_log(
    bundle: Path, trial_id: str, text: str | None, member: str = "agent.stdout.log"
) -> None:
    archive = bundle / "logs" / f"{trial_id}.tar.gz"
    archive.parent.mkdir(exist_ok=True)
    with tarfile.open(archive, "w:gz") as tar:
        data = text.encode("utf-8") if text is not None else b""
        info = tarfile.TarInfo(name=member)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    refresh_hashes(bundle, [f"logs/{trial_id}.tar.gz"])


@pytest.mark.parametrize(
    "log_line",
    [
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIfake",
        "arn:aws:sts::123 AKIAIOSFODNN7EXAMPLE",
        '{"access_token": "sk-live-abcd1234"}',
    ],
)
def test_log_archive_rejects_secrets(catalog: SpecCatalog, tmp_path: Path, log_line: str) -> None:
    bundle = _log_bundle(catalog, tmp_path)
    _add_log(bundle, "trial-001", f"before\n{log_line}\nafter\n")
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["secret"]
    assert problems[0].path.startswith("logs/trial-001.tar.gz#")


def test_log_archive_rejects_unsafe_member_path(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = _log_bundle(catalog, tmp_path)
    _add_log(bundle, "trial-001", "harmless", member="../escaped.txt")
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["layout"]
    assert "../escaped.txt" in problems[0].message


def test_log_archive_without_secrets_passes(catalog: SpecCatalog, tmp_path: Path) -> None:
    bundle = _log_bundle(catalog, tmp_path)
    _add_log(bundle, "trial-001", "plain output\nexit code 0\n")
    assert validate_bundle(bundle, catalog) == []


def test_log_archive_reports_unparseable_json_member(
    catalog: SpecCatalog, tmp_path: Path
) -> None:
    bundle = _log_bundle(catalog, tmp_path)
    _add_log(bundle, "trial-001", "[" * 10000 + "]" * 10000, member="output.json")
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["layout"]
    assert "nested too deeply" in problems[0].message


def test_log_archive_tolerates_oversized_json_integer(
    catalog: SpecCatalog, tmp_path: Path
) -> None:
    bundle = _log_bundle(catalog, tmp_path)
    _add_log(bundle, "trial-001", '{"n": ' + "9" * 5000 + "}\n", member="output.json")
    assert validate_bundle(bundle, catalog) == []


def test_log_archive_scans_json_member_with_oversized_integer(
    catalog: SpecCatalog, tmp_path: Path
) -> None:
    # An escaped key plus an oversized integer must still reach the key scan:
    # the member parses (parse_int bypasses the digit limit) instead of being
    # dropped as unparseable.
    bundle = _log_bundle(catalog, tmp_path)
    _add_log(
        bundle,
        "trial-001",
        '{"access\\u005ftoken": "sk-live-abcd1234", "n": ' + "9" * 5000 + "}",
        member="output.json",
    )
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["secret"]
    assert "access_token" in problems[0].message


def test_log_archive_rejects_member_over_limit(
    catalog: SpecCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ai_benchmark.submission.validate.LOG_TOTAL_LIMIT_BYTES", 10)
    bundle = _log_bundle(catalog, tmp_path)
    _add_log(bundle, "trial-001", "x" * 20)
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["layout"]
    assert "exceeds" in problems[0].message


def test_log_archive_rejects_cumulative_size_over_limit(
    catalog: SpecCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ai_benchmark.submission.validate.LOG_TOTAL_LIMIT_BYTES", 10)
    bundle = _log_bundle(catalog, tmp_path)
    archive = bundle / "logs" / "trial-001.tar.gz"
    archive.parent.mkdir(exist_ok=True)
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("one.log", "two.log"):
            info = tarfile.TarInfo(name=name)
            info.size = 6
            tar.addfile(info, io.BytesIO(b"012345"))
    refresh_hashes(bundle, ["logs/trial-001.tar.gz"])
    problems = validate_bundle(bundle, catalog)
    assert [p.code for p in problems] == ["layout"]


# --- manifest back-compat -----------------------------------------------------


def test_manifest_without_suite_hash_still_loads() -> None:
    manifest = RunManifest.model_validate(
        {
            "run_id": "run-old-001",
            "created_at": "2026-10-01T00:00:00Z",
            "platform_version": "0.3.0",
            "seed": 1,
            "auth_mode": "subscription",
            "experiment": {},
            "task_snapshot": {},
            "trial_ids": [],
            "host": {},
        }
    )
    assert manifest.acceptance_suite_sha256 is None


# --- CLI ----------------------------------------------------------------------


def test_cli_valid_bundle_exits_zero() -> None:
    result = runner.invoke(app, [*DIRS, "validate-submission", str(FIXTURE_BUNDLE)])
    assert result.exit_code == 0, result.output
    assert "ok" in result.output


def test_cli_broken_bundle_exits_one(tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    (bundle / "trials" / "extra.txt").write_text("unexpected", encoding="utf-8")
    result = runner.invoke(app, [*DIRS, "validate-submission", str(bundle)])
    assert result.exit_code == 1
    assert "layout" in result.output


def test_cli_missing_bundle_is_usage_error() -> None:
    result = runner.invoke(app, [*DIRS, "validate-submission", "/does/not/exist"])
    assert result.exit_code == 2
    assert "not a bundle directory" in result.output


def test_cli_batch_flags_same_run_id_across_bundles(tmp_path: Path) -> None:
    first = copy_bundle(tmp_path)
    second = tmp_path / "other" / "submissions" / "task-management-api" / "run-fixture-001"
    second.parent.mkdir(parents=True)
    shutil.copytree(FIXTURE_BUNDLE, second)
    result = runner.invoke(app, [*DIRS, "validate-submission", str(first), str(second)])
    assert result.exit_code == 1, result.output
    assert "duplicate" in result.output
    assert f"ok {first}" in result.output
