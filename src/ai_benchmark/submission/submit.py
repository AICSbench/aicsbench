"""Pack a stored run into a submission bundle and open its pull request.

See docs/phase-5/tasks/03-submit-command.md and
docs/phase-5/schemas.md#submission-bundle. ``results/`` is only read; the
bundle holds sanitized copies.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import re
import shutil
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from ai_benchmark.config.loader import SpecCatalog
from ai_benchmark.config.settings import PlatformSettings
from ai_benchmark.errors import ConfigurationError, SubmissionError
from ai_benchmark.models.enums import TrialStatus
from ai_benchmark.models.results import RunManifest, TrialResult
from ai_benchmark.storage.base import ResultFilter
from ai_benchmark.storage.json_store import JsonResultStore
from ai_benchmark.submission.github import GitHub
from ai_benchmark.submission.models import BUNDLE_ID_PATTERN, Problem, SubmissionInfo, Submitter
from ai_benchmark.submission.secrets import find_secrets, scan_text
from ai_benchmark.submission.suite import suite_sha256
from ai_benchmark.submission.validate import (
    CUSTOM_ENDPOINT_HOST,
    PUBLIC_ENDPOINT_HOSTS,
    validate_bundle,
)

DEFAULT_DATA_REPO = "aicsbench/data"
HOST_KEYS = ("platform", "python", "machine")
LICENCE_LINE = (
    "Submitted result data is published under CC BY 4.0 "
    "(https://creativecommons.org/licenses/by/4.0/); by opening this pull request "
    "I agree to publish it under that licence."
)


@dataclass
class Bundle:
    """A bundle built in a temporary folder, plus what building it found."""

    path: Path
    task_id: str
    run_id: str
    trials: list[TrialResult]
    warnings: list[str] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)


def build_bundle(
    settings: PlatformSettings,
    catalog: SpecCatalog,
    run_id: str,
    root: Path,
    *,
    submitter: Submitter,
    notes: str = "",
    with_logs: bool = False,
    now: datetime | None = None,
) -> Bundle:
    """Write ``root/submissions/<task_id>/<run_id>/`` for a finished run.

    Raises :class:`SubmissionError` when the run cannot be packed. A secret
    found in a log file is returned in ``Bundle.problems`` instead, so the
    caller can report it with the validator's problems.
    """
    manifest, trials = _load_run(settings, run_id)
    task_id = str(manifest.task_snapshot.get("id"))
    task_version = str(manifest.task_snapshot.get("version"))
    for value in (task_id, run_id):
        if re.fullmatch(BUNDLE_ID_PATTERN, value) is None:
            raise SubmissionError(f"'{value}' does not match {BUNDLE_ID_PATTERN}")

    if manifest.acceptance_suite_sha256 is not None:
        suite_hash, suite_source = manifest.acceptance_suite_sha256, "run"
    else:
        try:
            suite_hash = suite_sha256(catalog.get_task(task_id))
        except (OSError, ConfigurationError) as exc:
            raise SubmissionError(f"cannot hash the acceptance suite of {task_id}: {exc}") from None
        suite_source = "submit"

    bundle = Bundle(root / "submissions" / task_id / run_id, task_id, run_id, trials)
    bundle.path.mkdir(parents=True)
    replace = _Redactor(settings, run_id, _private_endpoint_hosts(manifest, trials))

    archived: set[str] = set()
    if with_logs:
        (bundle.path / "logs").mkdir()
        for trial in trials:
            if _pack_logs(settings, trial.trial_id, run_id, bundle, replace):
                archived.add(trial.trial_id)

    manifest_data = manifest.model_dump(mode="json")
    manifest_data["host"] = {k: v for k, v in manifest.host.items() if k in HOST_KEYS}
    for agent in manifest_data["experiment"].get("agents") or []:
        endpoint = agent.get("endpoint") if isinstance(agent, dict) else None
        if isinstance(endpoint, dict) and isinstance(endpoint.get("base_url"), str):
            endpoint["base_url"] = replace.base_url(endpoint["base_url"])
    _write_json(bundle.path / "manifest.json", replace.json(manifest_data))
    (bundle.path / "trials").mkdir()
    for trial in trials:
        data = trial.model_dump(mode="json")
        data["agent"]["endpoint_host"] = replace.endpoint_host(trial.agent.endpoint_host)
        data["log_files"] = {
            name: _bundle_log_path(settings, run_id, trial.trial_id, value)
            if trial.trial_id in archived
            else ""
            for name, value in trial.log_files.items()
        }
        _write_json(bundle.path / "trials" / f"{trial.trial_id}.json", replace.json(data))

    files = {
        file.relative_to(bundle.path).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(bundle.path.rglob("*"))
        if file.is_file()
    }
    try:
        info = SubmissionInfo(
            schema_version=1,
            run_id=run_id,
            task_id=task_id,
            task_version=task_version,
            submitted_at=(now or datetime.now(timezone.utc)).replace(microsecond=0),
            submitter=submitter,
            notes=notes,
            platform_version=manifest.platform_version,
            platform_git_commit=manifest.platform_git_commit,
            acceptance_suite_sha256=suite_hash,
            suite_hash_source=suite_source,
            logs_included=bool(archived),
            files=files,
        )
    except ValidationError as exc:
        detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise SubmissionError(f"invalid submission details: {detail}") from None
    if with_logs and not archived:
        (bundle.path / "logs").rmdir()
    (bundle.path / "submission.json").write_text(
        info.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return bundle


def _load_run(settings: PlatformSettings, run_id: str) -> tuple[RunManifest, list[TrialResult]]:
    store = JsonResultStore(settings.results_dir)
    manifest = store.load_manifest(run_id)
    trials = {t.trial_id: t for t in store.iter_trials(ResultFilter(run_id=run_id))}
    unfinished = [
        trial_id
        for trial_id in manifest.trial_ids
        if trial_id not in trials
        or trials[trial_id].status in (TrialStatus.PENDING, TrialStatus.RUNNING)
    ]
    if unfinished:
        raise SubmissionError(
            f"run '{run_id}' is not finished; pending or running: {', '.join(unfinished)}"
        )
    return manifest, [trials[trial_id] for trial_id in manifest.trial_ids]


class _Redactor:
    """Replaces host paths and private gateway hosts in everything ``submit`` writes.

    Applied to every string, keys included, of the manifest and the trials,
    and to log text. The run's results and workspace folders and the home
    folder become ``<results>``, ``<workspace>`` and ``~``: longest folder
    first, so the run's folders win over the home folder they may sit in, and
    only as a whole path segment, so ``/home/al`` never rewrites ``/home/alice``.
    A custom endpoint host outside ``PUBLIC_ENDPOINT_HOSTS`` becomes ``custom``
    wherever it appears (``agent.endpoint_host``, the experiment's
    ``base_url``, command lines in ``raw``, logs), so a private gateway address
    is never published.
    """

    def __init__(self, settings: PlatformSettings, run_id: str, private_hosts: set[str]) -> None:
        folders = [
            (settings.results_dir / run_id, "<results>"),
            (settings.workspaces_dir / run_id, "<workspace>"),
        ]
        with contextlib.suppress(RuntimeError):
            folders.append((Path.home(), "~"))
        pairs = {
            (form, token)
            for folder, token in folders
            for form in (str(folder.absolute()), str(folder.resolve()))
            if form.strip("/")
        }
        self.rules = [
            (re.compile(re.escape(form) + r"(?![^/\s\"'<>:;,)\]}])"), token)
            for form, token in sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)
        ]
        self.private_hosts = private_hosts
        ordered = sorted(private_hosts, key=len, reverse=True)
        self.host_rules = [
            (
                re.compile(rf"(?<![A-Za-z0-9-]){re.escape(host)}(?![A-Za-z0-9-])", re.IGNORECASE),
                CUSTOM_ENDPOINT_HOST,
            )
            for host in ordered
        ]
        # A single-label host (a Docker service name such as "gateway") would
        # match ordinary words, so in free text it is replaced only inside a URL
        # (after "//"); dotted hosts (FQDNs, IPs) are replaced everywhere, and
        # the fields that name the host always are.
        self.rules += [
            rule
            if "." in host
            else (
                re.compile(rf"(?<=//){re.escape(host)}(?![A-Za-z0-9.-])", re.IGNORECASE),
                CUSTOM_ENDPOINT_HOST,
            )
            for rule, host in zip(self.host_rules, ordered, strict=True)
        ]

    def endpoint_host(self, host: str | None) -> str | None:
        return CUSTOM_ENDPOINT_HOST if host and host.lower() in self.private_hosts else host

    def base_url(self, url: str) -> str:
        for pattern, token in self.host_rules:
            url = pattern.sub(token, url)
        return url

    def text(self, value: str) -> str:
        for pattern, token in self.rules:
            value = pattern.sub(token, value)
        return value

    def json(self, node: Any) -> Any:
        if isinstance(node, dict):
            return {self.text(str(k)): self.json(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self.json(item) for item in node]
        if isinstance(node, str):
            return self.text(node)
        return node


def _private_endpoint_hosts(manifest: RunManifest, trials: list[TrialResult]) -> set[str]:
    """Custom endpoint hosts of this run that may not be published."""
    hosts = {trial.agent.endpoint_host for trial in trials}
    agents = manifest.experiment.get("agents")
    for agent in agents if isinstance(agents, list) else []:
        endpoint = agent.get("endpoint") if isinstance(agent, dict) else None
        base_url = endpoint.get("base_url") if isinstance(endpoint, dict) else None
        if isinstance(base_url, str):
            hosts.add(urlsplit(base_url).hostname)
    return {
        host.lower()
        for host in hosts
        if host and host.lower() not in PUBLIC_ENDPOINT_HOSTS and host != CUSTOM_ENDPOINT_HOST
    }


def _trial_log_dir(settings: PlatformSettings, run_id: str, trial_id: str) -> Path:
    return settings.workspaces_dir / run_id / trial_id / "logs"


def _bundle_log_path(settings: PlatformSettings, run_id: str, trial_id: str, value: str) -> str:
    """``logs/<trial_id>.tar.gz#<name>`` for a log file recorded by the run."""
    path = Path(value)
    log_dir = _trial_log_dir(settings, run_id, trial_id)
    try:
        name = path.resolve().relative_to(log_dir.resolve()).as_posix()
    except ValueError:
        name = path.name
    return f"logs/{trial_id}.tar.gz#{name}"


def _pack_logs(
    settings: PlatformSettings, trial_id: str, run_id: str, bundle: Bundle, replace: _Redactor
) -> bool:
    """Scan, then pack a trial's logs folder. False when there is nothing to pack."""
    log_dir = _trial_log_dir(settings, run_id, trial_id)
    archive_name = f"logs/{trial_id}.tar.gz"
    if not log_dir.is_dir():
        bundle.warnings.append(f"no logs folder for {trial_id}; it gets no log archive")
        return False
    members: list[tuple[str, bytes]] = []
    for file in sorted(log_dir.rglob("*")):
        name = file.relative_to(log_dir).as_posix()
        if file.is_symlink():
            bundle.warnings.append(f"skipped symlink in the logs of {trial_id}: {name}")
            continue
        if not file.is_file():
            continue
        text = replace.text(file.read_bytes().decode("utf-8", errors="replace"))
        matches = scan_text(text)
        if name.endswith(".json"):
            with contextlib.suppress(ValueError, RecursionError):
                matches += find_secrets(json.loads(text, parse_int=str))
        if matches:
            bundle.problems.append(
                Problem(
                    "secret", f"{archive_name}#{name}", f"possible secret: {', '.join(matches)}"
                )
            )
        members.append((name, text.encode("utf-8")))
    if not members:
        bundle.warnings.append(f"the logs folder of {trial_id} is empty; it gets no log archive")
        return False
    if bundle.problems:
        return False  # never compress a log with a secret in it
    buffer = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar,
    ):
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tar.addfile(info, io.BytesIO(data))
    (bundle.path / archive_name).write_bytes(buffer.getvalue())
    return True


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def pr_title(task_id: str, run_id: str) -> str:
    """Also the commit message."""
    return f"data: add {task_id} run {run_id}"


def _harness(trial: TrialResult) -> str:
    return f"{trial.agent.name} {trial.agent.version or 'unknown'}"


def pr_body(bundle: Bundle) -> str:
    trials = bundle.trials

    def values(get: Any) -> str:
        return ", ".join(sorted({get(t) for t in trials})) or "-"

    passed = sum(1 for t in trials if t.status is TrialStatus.PASSED)
    return "\n".join(
        [
            f"Run `{bundle.run_id}` of task `{bundle.task_id}`, submitted with `aicsbench submit`.",
            "",
            f"- Harnesses: {values(_harness)}",
            f"- Models: {values(lambda t: t.agent.model_label)}",
            f"- Stacks: {values(lambda t: t.environment.stack)}",
            f"- Trials: {len(trials)}",
            f"- Passed: {passed}",
            "",
            LICENCE_LINE,
            "",
        ]
    )


def open_pull_request(
    bundle: Bundle,
    catalog: SpecCatalog,
    github: GitHub,
    data_repo: str,
    workdir: Path,
    *,
    author_name: str,
    author_handle: str,
) -> tuple[str | None, list[Problem]]:
    """Fork, clone, validate against upstream, then commit, push and open the PR.

    Returns ``(url, [])`` or ``(None, problems)``; nothing is pushed when the
    bundle has problems, including a run already in the upstream repository.
    """
    github.fork(data_repo)
    login = github.login()
    fork_dir, upstream_dir = workdir / "fork", workdir / "upstream"
    github.clone(f"{login}/{data_repo.split('/')[-1]}", fork_dir)
    # The fork may be behind upstream, so duplicates are checked against upstream.
    github.clone(data_repo, upstream_dir)
    merged = upstream_dir / "submissions"
    problems = validate_bundle(bundle.path, catalog, against=merged if merged.is_dir() else None)
    if problems:
        return None, problems

    branch = f"submit/{bundle.run_id}"
    title = pr_title(bundle.task_id, bundle.run_id)
    github.create_branch(fork_dir, branch)
    target = fork_dir / "submissions" / bundle.task_id / bundle.run_id
    if target.exists():
        raise SubmissionError(f"{target.relative_to(fork_dir)} already exists in the fork")
    shutil.copytree(bundle.path, target)
    github.commit(fork_dir, title, author_name, f"{author_handle}@users.noreply.github.com")
    github.push(fork_dir, branch)
    url = github.create_pr(data_repo, f"{login}:{branch}", title, pr_body(bundle), fork_dir)
    return url, []
