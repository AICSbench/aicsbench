"""``aicsbench submit`` (docs/phase-5/tasks/03-submit-command.md).

GitHub and git go through a fake; no test touches the network or real ``gh``.
"""

from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import ai_benchmark.cli.app as cli
from ai_benchmark.models.enums import TrialStatus
from ai_benchmark.models.results import RunManifest, TrialResult
from ai_benchmark.storage.json_store import JsonResultStore

from .test_submission import DIRS, FIXTURE_BUNDLE

RUN_ID = "run-submit-001"
TASK_ID = "task-management-api"
runner = CliRunner()


class FakeGitHub:
    """Records calls; clones copy prepared folders instead of reaching GitHub."""

    def __init__(self, repos: dict[str, Path] | None = None, logged_in: bool = True) -> None:
        self.repos = repos or {}
        self.is_logged_in = logged_in
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.pushed_branch: Path | None = None
        """Files of a ``submit/<run>`` branch already on the fork, if any."""

    def logged_in(self) -> bool:
        return self.is_logged_in

    def login(self) -> str:
        return "octo"

    def git_user_name(self) -> str | None:
        return "Git Name"

    def fork(self, repo: str) -> None:
        self.calls.append(("fork", (repo,)))

    def clone(self, repo: str, dest: Path) -> None:
        self.calls.append(("clone", (repo,)))
        source = self.repos.get(repo)
        if source is not None:
            shutil.copytree(source, dest)
        else:
            dest.mkdir(parents=True)

    def create_branch(self, repo_dir: Path, branch: str) -> None:
        self.calls.append(("branch", (branch,)))

    def commit(self, repo_dir: Path, message: str, name: str, email: str) -> None:
        self.committed = sorted(p.relative_to(repo_dir).as_posix() for p in repo_dir.rglob("*"))
        self.calls.append(("commit", (message, name, email)))

    def remote_branch_exists(self, repo_dir: Path, branch: str) -> bool:
        return self.pushed_branch is not None

    def checkout_remote_branch(self, repo_dir: Path, branch: str) -> None:
        self.calls.append(("checkout", (branch,)))
        assert self.pushed_branch is not None
        shutil.copytree(self.pushed_branch, repo_dir, dirs_exist_ok=True)

    def changed_paths(self, repo_dir: Path, base_repo: str) -> list[str]:
        assert self.pushed_branch is not None
        return sorted(
            p.relative_to(self.pushed_branch).as_posix()
            for p in self.pushed_branch.rglob("*")
            if p.is_file()
        )

    def push(self, repo_dir: Path, branch: str) -> None:
        self.calls.append(("push", (branch,)))

    def create_pr(self, repo: str, head: str, title: str, body: str, cwd: Path) -> str:
        self.calls.append(("pr create", (repo, head, title)))
        self.body = body
        return "https://github.com/aicsbench/data/pull/1"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A finished run in tmp results with host paths, logs and extra host keys."""
    results, workspaces = tmp_path / "results", tmp_path / "workspaces"
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BENCHMARK_WORKSPACES_DIR", str(workspaces))
    manifest = RunManifest.model_validate_json((FIXTURE_BUNDLE / "manifest.json").read_text())
    manifest = manifest.model_copy(
        update={
            "run_id": RUN_ID,
            "host": {**manifest.host, "hostname": "secret-box", "cwd": str(home / "bench")},
            "experiment": {**manifest.experiment, "config": str(home / "exp.yaml")},
        }
    )
    store = JsonResultStore(results)
    store.save_manifest(manifest)
    for trial_id in manifest.trial_ids:
        trial = TrialResult.model_validate_json(
            (FIXTURE_BUNDLE / "trials" / f"{trial_id}.json").read_text()
        )
        log_dir = workspaces / RUN_ID / trial_id / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "agent.stdout.log").write_text(f"working in {log_dir.parent}/workspace\n")
        trial = trial.model_copy(
            update={
                "run_id": RUN_ID,
                "log_files": {"agent_stdout": str(log_dir / "agent.stdout.log")},
                "raw": {"cwd": str(workspaces / RUN_ID / trial_id / "workspace")},
                "error": f"see {results / RUN_ID}/trials",
            }
        )
        store.save_trial(trial)
    fake = FakeGitHub()
    monkeypatch.setattr(cli, "GitHub", lambda: fake)
    return {"tmp": tmp_path, "results": results, "workspaces": workspaces, "fake": fake}


def submit(env: dict[str, Any], *args: str) -> Any:
    base = [*DIRS, "--results-dir", str(env["results"])]
    return runner.invoke(cli.app, [*base, "submit", "--run", RUN_ID, *args])


def files_under(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


def edit_trial(env: dict[str, Any], **update: Any) -> None:
    path = env["results"] / RUN_ID / "trials" / "trial-001.json"
    trial = TrialResult.model_validate_json(path.read_text())
    path.write_text(trial.model_copy(update=update).model_dump_json())


def test_dry_run_prints_bundle_and_writes_nothing(env: dict[str, Any]) -> None:
    before = files_under(env["tmp"])
    result = submit(env, "--dry-run")
    assert result.exit_code == 0, result.output
    assert f"submissions/{TASK_ID}/{RUN_ID}/submission.json" in result.output
    assert f"submissions/{TASK_ID}/{RUN_ID}/trials/trial-002.json" in result.output
    assert "validation: ok" in result.output
    assert "duplicate check skipped" in result.output
    assert files_under(env["tmp"]) == before
    assert env["fake"].calls == []


def test_out_writes_sanitized_bundle_that_validates(env: dict[str, Any]) -> None:
    out = env["tmp"] / "out"
    notes = f"M3, models in {env['tmp'] / 'home' / 'models'}"
    result = submit(env, "--out", str(out), "--with-logs", "--github", "@octo", "--notes", notes)
    assert result.exit_code == 0, result.output
    bundle = out / "submissions" / TASK_ID / RUN_ID
    assert files_under(bundle) == {
        "submission.json",
        "manifest.json",
        "trials",
        "trials/trial-001.json",
        "trials/trial-002.json",
        "logs",
        "logs/trial-001.tar.gz",
        "logs/trial-002.tar.gz",
    }

    check = runner.invoke(cli.app, [*DIRS, "validate-submission", str(bundle)])
    assert check.exit_code == 0, check.output

    # No host path anywhere, in JSON files or inside the log archives.
    host = str(env["tmp"])
    for file in bundle.rglob("*.json"):
        assert host not in file.read_text(), file
    for archive in bundle.glob("logs/*.tar.gz"):
        trial_id = archive.name.removesuffix(".tar.gz")
        with tarfile.open(archive) as tar:
            assert tar.getnames() == ["agent.stdout.log"]
            member = tar.extractfile("agent.stdout.log")
            assert member is not None
            assert member.read().decode() == f"working in <workspace>/{trial_id}/workspace\n"

    manifest = json.loads((bundle / "manifest.json").read_text())
    assert set(manifest["host"]) == {"platform", "python", "machine"}
    assert manifest["experiment"]["config"] == "~/exp.yaml"
    trial = json.loads((bundle / "trials" / "trial-001.json").read_text())
    assert trial["log_files"] == {"agent_stdout": "logs/trial-001.tar.gz#agent.stdout.log"}
    assert trial["raw"] == {"cwd": "<workspace>/trial-001/workspace"}
    assert trial["error"] == "see <results>/trials"
    info = json.loads((bundle / "submission.json").read_text())
    assert info["submitter"] == {"name": "Git Name", "github": "octo"}
    assert info["suite_hash_source"] == "run"
    assert info["notes"] == "M3, models in ~/models"
    assert info["logs_included"] is True
    # results/ is never edited.
    stored = json.loads((env["results"] / RUN_ID / "trials" / "trial-001.json").read_text())
    assert stored["log_files"]["agent_stdout"].startswith(host)


def test_without_logs_keeps_log_keys_empty_and_hashes_suite_at_submit(
    env: dict[str, Any],
) -> None:
    path = env["results"] / RUN_ID / "manifest.json"
    data = json.loads(path.read_text())
    data["acceptance_suite_sha256"] = None
    path.write_text(json.dumps(data))
    out = env["tmp"] / "out"
    result = submit(env, "--out", str(out))
    assert result.exit_code == 0, result.output
    bundle = out / "submissions" / TASK_ID / RUN_ID
    assert not (bundle / "logs").exists()
    assert json.loads((bundle / "trials" / "trial-001.json").read_text())["log_files"] == {
        "agent_stdout": ""
    }
    info = json.loads((bundle / "submission.json").read_text())
    assert info["suite_hash_source"] == "submit"
    assert info["logs_included"] is False
    assert (
        info["acceptance_suite_sha256"]
        == json.loads((FIXTURE_BUNDLE / "submission.json").read_text())["acceptance_suite_sha256"]
    )


def test_missing_log_folder_is_a_warning(env: dict[str, Any]) -> None:
    shutil.rmtree(env["workspaces"] / RUN_ID / "trial-002")
    out = env["tmp"] / "out"
    result = submit(env, "--out", str(out), "--with-logs")
    assert result.exit_code == 0, result.output
    assert "no logs folder for trial-002" in result.output
    bundle = out / "submissions" / TASK_ID / RUN_ID
    assert sorted(p.name for p in (bundle / "logs").iterdir()) == ["trial-001.tar.gz"]
    trial = json.loads((bundle / "trials" / "trial-002.json").read_text())
    assert trial["log_files"] == {"agent_stdout": ""}


def test_secret_in_raw_is_refused_and_nothing_written(env: dict[str, Any]) -> None:
    edit_trial(env, raw={"note": "key sk-ant-" + "a" * 30})
    out = env["tmp"] / "out"
    result = submit(env, "--out", str(out))
    assert result.exit_code == 1
    assert "secret trials/trial-001.json" in result.output
    assert not out.exists()


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("agent.stdout.log", "export ANTHROPIC_API_KEY=abc123def\n"),
        ("events.jsonl", '{"type": "auth", "access_token": "tok"}\n'),
        ("config.json", '{"nested": {"Password": "hunter22"}}'),
    ],
)
def test_secret_in_log_is_refused_and_nothing_written(
    env: dict[str, Any], name: str, content: str
) -> None:
    (env["workspaces"] / RUN_ID / "trial-002" / "logs" / name).write_text(content)
    out = env["tmp"] / "out"
    result = submit(env, "--out", str(out), "--with-logs")
    assert result.exit_code == 1
    assert f"secret logs/trial-002.tar.gz#{name}" in result.output
    assert not out.exists()


def test_unfinished_run_is_refused(env: dict[str, Any]) -> None:
    edit_trial(env, status=TrialStatus.RUNNING)
    result = submit(env, "--dry-run")
    assert result.exit_code == 1
    assert "trial-001" in result.output


def test_against_rejects_duplicate_in_out_mode(env: dict[str, Any]) -> None:
    data = env["tmp"] / "data"
    first = submit(env, "--out", str(data))
    assert first.exit_code == 0, first.output
    result = submit(env, "--dry-run", "--against", str(data))
    assert result.exit_code == 1
    assert "duplicate" in result.output


def test_pr_mode_issues_commands_in_order(env: dict[str, Any]) -> None:
    fake = env["fake"]
    path = env["results"] / RUN_ID / "trials" / "trial-001.json"
    trial = TrialResult.model_validate_json(path.read_text())
    local_model = str(env["tmp"] / "home" / "models" / "local")
    agent = trial.agent.model_copy(update={"resolved_model": local_model})
    path.write_text(trial.model_copy(update={"agent": agent}).model_dump_json())
    result = submit(env, "--name", "Ada")
    assert result.exit_code == 0, result.output
    assert "https://github.com/aicsbench/data/pull/1" in result.output
    title = f"data: add {TASK_ID} run {RUN_ID}"
    assert fake.calls == [
        ("fork", ("aicsbench/data",)),
        ("clone", ("octo/data",)),
        ("clone", ("aicsbench/data",)),
        ("branch", (f"submit/{RUN_ID}",)),
        ("commit", (title, "Ada", "octo@users.noreply.github.com")),
        ("push", (f"submit/{RUN_ID}",)),
        ("pr create", ("aicsbench/data", f"octo:submit/{RUN_ID}", title)),
    ]
    assert f"submissions/{TASK_ID}/{RUN_ID}/submission.json" in fake.committed
    for needle in ("codex 0.167.2", "gpt-6-luna", "fastapi", "Trials: 2", "Passed: ", "CC BY 4.0"):
        assert needle in fake.body
    # The PR body shows what the bundle shows, never a host path.
    assert "~/models/local" in fake.body
    assert str(env["tmp"]) not in fake.body


def test_pr_mode_resumes_branch_pushed_by_an_earlier_submit(
    env: dict[str, Any], tmp_path: Path
) -> None:
    earlier = tmp_path / "earlier"
    assert submit(env, "--out", str(earlier)).exit_code == 0
    fake = env["fake"]
    fake.pushed_branch = earlier
    # The local run changes after the push; the PR body describes the pushed files.
    path = env["results"] / RUN_ID / "trials" / "trial-001.json"
    trial = TrialResult.model_validate_json(path.read_text())
    agent = trial.agent.model_copy(update={"resolved_model": "changed-later"})
    path.write_text(trial.model_copy(update={"agent": agent}).model_dump_json())
    result = submit(env)
    assert result.exit_code == 0, result.output
    assert "gpt-6-luna" in fake.body
    assert "changed-later" not in fake.body
    assert "https://github.com/aicsbench/data/pull/1" in result.output
    title = f"data: add {TASK_ID} run {RUN_ID}"
    assert fake.calls == [
        ("fork", ("aicsbench/data",)),
        ("clone", ("octo/data",)),
        ("clone", ("aicsbench/data",)),
        ("checkout", (f"submit/{RUN_ID}",)),
        ("pr create", ("aicsbench/data", f"octo:submit/{RUN_ID}", title)),
    ]


def test_pr_mode_refuses_unrelated_branch_with_the_submission_name(
    env: dict[str, Any], tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "README.md").write_text("someone else's work\n")
    fake = env["fake"]
    fake.pushed_branch = other
    result = submit(env)
    assert result.exit_code == 1
    assert "not an earlier submission" in " ".join(result.output.split())
    assert [call[0] for call in fake.calls] == ["fork", "clone", "clone", "checkout"]


def test_pr_mode_refuses_run_already_merged_upstream_with_stale_fork(
    env: dict[str, Any], tmp_path: Path
) -> None:
    upstream = tmp_path / "upstream-data"
    merged = submit(env, "--out", str(upstream))
    assert merged.exit_code == 0, merged.output
    stale_fork = tmp_path / "fork-data"
    stale_fork.mkdir()
    (stale_fork / "README.md").write_text("old fork\n")
    fake = env["fake"]
    fake.repos = {"aicsbench/data": upstream, "octo/data": stale_fork}
    fake.calls.clear()

    result = submit(env)
    assert result.exit_code == 1
    assert "duplicate" in result.output
    assert [call[0] for call in fake.calls] == ["fork", "clone", "clone"]


def test_pr_mode_without_gh_login_points_to_out(env: dict[str, Any]) -> None:
    env["fake"].is_logged_in = False
    result = submit(env)
    assert result.exit_code == 1
    assert "--out DIR" in " ".join(result.output.split())
    assert env["fake"].calls == []


@pytest.mark.parametrize(
    ("host", "published"),
    [
        ("llm.corp.internal", "custom"),
        ("10.20.30.40", "custom"),
        ("gateway", "custom"),
        ("fd00::1", "custom"),
        ("openrouter.ai", "openrouter.ai"),
    ],
)
def test_private_endpoint_host_is_published_as_custom(
    env: dict[str, Any], host: str, published: str
) -> None:
    netloc = f"[{host}]" if ":" in host else host
    base_url = f"https://{netloc}:8443/v1"
    command = f"codex exec --config model_providers.bench.base_url={base_url} via gateway"
    for trial_id in ("trial-001", "trial-002"):
        path = env["results"] / RUN_ID / "trials" / f"{trial_id}.json"
        trial = TrialResult.model_validate_json(path.read_text())
        agent = trial.agent.model_copy(update={"provider": "corp", "endpoint_host": host})
        path.write_text(
            trial.model_copy(update={"agent": agent, "raw": {"cmd": command}}).model_dump_json()
        )
        log = env["workspaces"] / RUN_ID / trial_id / "logs" / "agent.stdout.log"
        log.write_text(f"POST {base_url}/responses\n")
    manifest_path = env["results"] / RUN_ID / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["experiment"]["agents"][0]["endpoint"] = {
        "provider": "corp",
        "base_url": base_url,
        "api_key_env": "CORP_KEY",
    }
    manifest_path.write_text(json.dumps(manifest))
    stored = {p: p.read_bytes() for p in (env["results"] / RUN_ID).rglob("*.json")}

    out = env["tmp"] / "out"
    result = submit(env, "--out", str(out), "--with-logs")
    assert result.exit_code == 0, result.output
    bundle = out / "submissions" / TASK_ID / RUN_ID
    check = runner.invoke(cli.app, [*DIRS, "validate-submission", str(bundle)])
    assert check.exit_code == 0, check.output

    url = f"https://{f'[{published}]' if ':' in host else published}:8443/v1"
    trial = json.loads((bundle / "trials" / "trial-001.json").read_text())
    assert trial["agent"]["endpoint_host"] == published
    endpoint = json.loads((bundle / "manifest.json").read_text())["experiment"]["agents"][0]
    assert endpoint["endpoint"]["base_url"] == url
    # Free text too; a single-label name is replaced only in URLs, never in words.
    assert trial["raw"]["cmd"] == command.replace(base_url, url)
    with tarfile.open(bundle / "logs" / "trial-001.tar.gz") as tar:
        member = tar.extractfile("agent.stdout.log")
        assert member is not None
        assert member.read().decode() == f"POST {url}/responses\n"
    if published == "custom":
        for file in bundle.rglob("*.json"):
            assert f"//{host}" not in file.read_text(), file
            if "." in host or ":" in host:
                assert host not in file.read_text(), file
    # results/ is never edited.
    assert {p: p.read_bytes() for p in (env["results"] / RUN_ID).rglob("*.json")} == stored
