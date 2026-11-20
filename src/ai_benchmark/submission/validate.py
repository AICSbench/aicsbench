"""Submission bundle validation (docs/phase-5/schemas.md#validation-rules).

``validate_bundle`` returns every problem it finds; an empty list means the
bundle is valid. Two modes:

- **Admission** (``check_catalog=True``, default): for new bundles in the
  data repository's CI and for ``submit``. The bundle must match a task
  version the running catalog ships, and the suite hash must match it.
- **Rebuild** (``check_catalog=False``): for bundles already merged, so a
  task version the catalog no longer ships stays valid. The ``task`` rule
  and the catalog part of the ``suite`` rule are skipped.
"""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ai_benchmark.config.loader import SpecCatalog
from ai_benchmark.errors import ConfigurationError
from ai_benchmark.models.enums import TrialStatus
from ai_benchmark.models.results import RunManifest, TrialResult
from ai_benchmark.submission.models import BUNDLE_ID_PATTERN, Problem, SubmissionInfo
from ai_benchmark.submission.secrets import find_secrets, scan_text
from ai_benchmark.submission.suite import suite_sha256

M = TypeVar("M", bound=BaseModel)

LOG_TOTAL_LIMIT_BYTES = 50 * 1024 * 1024
"""Maximum uncompressed size of one log archive, per docs/phase-5/schemas.md."""

LOG_READ_CHUNK_BYTES = 1024 * 1024
"""Log members are read in chunks so the size limit is enforced while reading."""


def validate_bundle(
    path: Path | str,
    catalog: SpecCatalog,
    *,
    against: Path | None = None,
    check_catalog: bool = True,
) -> list[Problem]:
    """Validate one bundle directory and return all problems found."""
    bundle = Path(path).resolve()
    if not bundle.is_dir():
        return [Problem("layout", str(path), "bundle is not a directory")]

    problems: list[Problem] = []
    submission = _load_model(bundle / "submission.json", SubmissionInfo, problems)
    manifest = _load_model(bundle / "manifest.json", RunManifest, problems)
    _reject_symlinks(bundle, problems)
    trials = _load_trials(bundle, problems)

    _check_layout(bundle, submission, manifest, problems)
    if submission is not None:
        _check_hashes(bundle, submission, problems)
        _check_ids(bundle, submission, manifest, trials, problems)
        _check_task_and_suite(
            bundle, submission, manifest, trials, catalog, check_catalog, problems
        )
    _check_comparable(trials, problems)
    _check_harness(trials, problems)
    _check_status(trials, problems)
    _check_secrets(bundle, submission, problems)
    _check_duplicate(bundle, manifest, against, problems)
    return problems


def _load_model(
    file: Path, model: type[M], problems: list[Problem], label: str | None = None
) -> M | None:
    name = label or file.name
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        # ValueError covers JSONDecodeError and, on Python 3.11+, the
        # plain ValueError json.loads raises for very long integers.
        problems.append(Problem("schema", name, f"{name} is missing or not valid JSON"))
        return None
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()[:5]
        )
        problems.append(Problem("schema", name, f"{name}: {detail}"))
        return None


def _reject_symlinks(bundle: Path, problems: list[Problem]) -> None:
    """Reject every symlink in the bundle.

    ``rglob`` does not follow symlinked directories, so their content would
    escape every scan.
    """
    for file in sorted(bundle.rglob("*")):
        if file.is_symlink():
            problems.append(
                Problem(
                    "layout",
                    file.relative_to(bundle).as_posix(),
                    "symlinks are not allowed in a bundle",
                )
            )


def _load_trials(bundle: Path, problems: list[Problem]) -> dict[str, TrialResult]:
    trials_dir = bundle / "trials"
    if not trials_dir.is_dir():
        problems.append(Problem("layout", "trials/", "the trials directory is missing"))
        return {}
    trials: dict[str, TrialResult] = {}
    embedded: dict[str, str] = {}
    for file in sorted(trials_dir.iterdir()):
        if file.is_file() and file.suffix == ".json":
            trial = _load_model(file, TrialResult, problems, label=f"trials/{file.name}")
            if trial is not None:
                if trial.trial_id in embedded:
                    problems.append(
                        Problem(
                            "ids",
                            f"trials/{file.name}",
                            f"duplicate trial_id '{trial.trial_id}' "
                            f"(also in {embedded[trial.trial_id]})",
                        )
                    )
                else:
                    embedded[trial.trial_id] = file.name
                trials[file.stem] = trial
    return trials


def _check_layout(
    bundle: Path,
    submission: SubmissionInfo | None,
    manifest: RunManifest | None,
    problems: list[Problem],
) -> None:
    where = bundle.name
    if not (bundle / "submission.json").is_file() or not (bundle / "manifest.json").is_file():
        problems.append(Problem("layout", where, "submission.json and manifest.json are required"))
    trial_ids = set(manifest.trial_ids) if manifest is not None else set()
    extra = [
        file.relative_to(bundle).as_posix()
        for file in sorted(bundle.rglob("*"))
        if file.is_file() and not _is_allowed_bundle_file(file, bundle, submission, trial_ids)
    ]
    if extra:
        problems.append(Problem("layout", where, f"unexpected files: {', '.join(extra)}"))
    if (
        submission is not None
        and submission.logs_included
        and not (any((bundle / "logs").glob("*.tar.gz")))
    ):
        problems.append(
            Problem("layout", "logs/", "logs_included is true but no logs/*.tar.gz is present")
        )


def _is_allowed_bundle_file(
    file: Path, bundle: Path, submission: SubmissionInfo | None, trial_ids: set[str]
) -> bool:
    relative = file.relative_to(bundle)
    parts = relative.parts
    if len(parts) == 1 and parts[0] in ("submission.json", "manifest.json"):
        return True
    if len(parts) == 2 and parts[0] == "trials" and parts[1].endswith(".json"):
        return True
    if len(parts) == 2 and parts[0] == "logs" and parts[1].endswith(".tar.gz"):
        if submission is None:
            return True
        stem = parts[1].removesuffix(".tar.gz")
        return submission.logs_included and stem in trial_ids
    return False


def _check_hashes(bundle: Path, submission: SubmissionInfo, problems: list[Problem]) -> None:
    for relative, expected in submission.files.items():
        file = bundle / relative
        try:
            escapes = (
                Path(relative).is_absolute()
                or ".." in Path(relative).parts
                or not file.resolve().is_relative_to(bundle)
            )
        except (OSError, RuntimeError) as exc:
            # A self-referencing symlink makes Path.resolve raise RuntimeError.
            problems.append(Problem("hash", relative, f"cannot resolve the file path: {exc}"))
            continue
        if escapes:
            problems.append(Problem("hash", relative, "files path escapes the bundle"))
            continue
        if not file.is_file():
            problems.append(
                Problem("hash", relative, "listed in files but missing from the bundle")
            )
            continue
        try:
            actual = hashlib.sha256(file.read_bytes()).hexdigest()
        except OSError as exc:
            problems.append(Problem("hash", relative, f"cannot read the file: {exc}"))
            continue
        if actual != expected.lower():
            problems.append(Problem("hash", relative, "SHA-256 does not match files"))
    if (bundle / "submission.json").is_file():
        hashed = set(submission.files)
        present = {
            file.relative_to(bundle).as_posix()
            for file in bundle.rglob("*")
            if file.is_file() and file.name != "submission.json"
        }
        unlisted = sorted(present - hashed)
        if unlisted:
            problems.append(Problem("hash", ", ".join(unlisted), "present but missing from files"))


def _check_ids(
    bundle: Path,
    submission: SubmissionInfo,
    manifest: RunManifest | None,
    trials: dict[str, TrialResult],
    problems: list[Problem],
) -> None:
    run_ids = {submission.run_id, bundle.name}
    task_ids = {submission.task_id, bundle.parent.name}
    task_versions = {submission.task_version}
    if manifest is not None:
        run_ids.add(manifest.run_id)
        task_ids.add(str(manifest.task_snapshot.get("id")))
        task_versions.add(str(manifest.task_snapshot.get("version")))
        platform_mismatches = [
            f"{field} disagrees with the manifest"
            for field in ("platform_version", "platform_git_commit")
            if getattr(manifest, field) != getattr(submission, field)
        ]
        if platform_mismatches:
            problems.append(Problem("ids", "submission.json", "; ".join(platform_mismatches)))
    for stem, trial in trials.items():
        task_ids.add(trial.task_id)
        task_versions.add(trial.task_version)
        run_ids.add(trial.run_id)
        if trial.trial_id != stem:
            problems.append(
                Problem(
                    "ids",
                    f"trials/{stem}.json",
                    f"embedded trial_id '{trial.trial_id}' does not match the file name",
                )
            )
    if len(run_ids) > 1:
        problems.append(
            Problem(
                "ids",
                bundle.name,
                f"run_id disagrees: {', '.join(sorted(run_ids))}",
            )
        )
    if len(task_ids) > 1:
        problems.append(
            Problem(
                "ids",
                bundle.parent.name,
                f"task_id disagrees: {', '.join(sorted(task_ids))}",
            )
        )
    if len(task_versions) > 1:
        problems.append(
            Problem(
                "ids",
                "submission.json",
                f"task_version disagrees: {', '.join(sorted(task_versions))}",
            )
        )
    trial_ids = sorted(p.stem for p in (bundle / "trials").glob("*.json") if p.is_file())
    if manifest is not None and sorted(manifest.trial_ids) != trial_ids:
        problems.append(
            Problem(
                "ids",
                "manifest.json",
                "manifest trial_ids do not match the trial files: "
                f"manifest {sorted(manifest.trial_ids)}, files {trial_ids}",
            )
        )
    for name in (bundle.name, bundle.parent.name):
        if not _matches_bundle_id(name):
            problems.append(Problem("ids", name, f"'{name}' does not match {BUNDLE_ID_PATTERN}"))


def _matches_bundle_id(name: str) -> bool:
    return re.fullmatch(BUNDLE_ID_PATTERN, name) is not None


def _check_task_and_suite(
    bundle: Path,
    submission: SubmissionInfo,
    manifest: RunManifest | None,
    trials: dict[str, TrialResult],
    catalog: SpecCatalog,
    check_catalog: bool,
    problems: list[Problem],
) -> None:
    spec = None
    if check_catalog:
        try:
            spec = catalog.get_task(submission.task_id)
        except ConfigurationError as exc:
            problems.append(Problem("task", bundle.name, str(exc)))
        else:
            if spec.version != submission.task_version:
                problems.append(
                    Problem(
                        "task",
                        "submission.json",
                        f"task version '{submission.task_version}' is not in the catalog "
                        f"(ships '{spec.version}')",
                    )
                )
    recorded = manifest.acceptance_suite_sha256 if manifest is not None else None
    if (submission.suite_hash_source == "run" or recorded is not None) and (
        recorded != submission.acceptance_suite_sha256
    ):
        problems.append(
            Problem(
                "suite",
                "manifest.json",
                "manifest acceptance_suite_sha256 does not match submission.json",
            )
        )
    if check_catalog and spec is not None:
        try:
            catalog_hash = suite_sha256(spec)
        except (OSError, ConfigurationError) as exc:
            problems.append(Problem("suite", "submission.json", f"cannot hash suite: {exc}"))
        else:
            if catalog_hash != submission.acceptance_suite_sha256:
                problems.append(
                    Problem(
                        "suite",
                        "submission.json",
                        f"acceptance_suite_sha256 does not match the catalog's suite "
                        f"({catalog_hash})",
                    )
                )


def _check_comparable(trials: dict[str, TrialResult], problems: list[Problem]) -> None:
    keys = {trial.comparability_key() for trial in trials.values()}
    if len(keys) > 1:
        problems.append(
            Problem("comparable", "trials/", "trials do not share one comparability_key")
        )


def _check_harness(trials: dict[str, TrialResult], problems: list[Problem]) -> None:
    for stem, trial in sorted(trials.items()):
        missing = [
            field
            for field, value in (
                ("agent.name", trial.agent.name),
                ("agent.version", trial.agent.version),
            )
            if not value
        ]
        if missing:
            problems.append(
                Problem(
                    "harness",
                    f"trials/{stem}.json",
                    f"missing {', '.join(missing)}; public trials need a harness version",
                )
            )


def _check_status(trials: dict[str, TrialResult], problems: list[Problem]) -> None:
    unfinished = sorted(
        stem
        for stem, trial in trials.items()
        if trial.status in (TrialStatus.PENDING, TrialStatus.RUNNING)
    )
    if unfinished:
        problems.append(
            Problem(
                "status",
                "trials/",
                f"unfinished trials: {', '.join(unfinished)}",
            )
        )


def _check_secrets(
    bundle: Path, submission: SubmissionInfo | None, problems: list[Problem]
) -> None:
    for file in sorted(bundle.rglob("*.json")):
        relative = file.relative_to(bundle).as_posix()
        if relative == "submission.json" and submission is not None:
            data: Any = submission.model_dump(mode="json")
        else:
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError, RecursionError):
                continue
        matches = find_secrets(data)
        if matches:
            problems.append(Problem("secret", relative, f"possible secret: {', '.join(matches)}"))
    for archive in sorted((bundle / "logs").glob("*.tar.gz")) if (bundle / "logs").is_dir() else []:
        _check_log_archive(bundle, archive, problems)


def _check_log_archive(bundle: Path, archive: Path, problems: list[Problem]) -> None:
    relative = archive.relative_to(bundle).as_posix()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            total = 0
            for member in tar:
                name = member.name
                if (
                    name.startswith("/")
                    or ".." in Path(name).parts
                    or member.issym()
                    or member.islnk()
                ):
                    problems.append(
                        Problem(
                            "layout",
                            f"{relative}#{name}",
                            f"unsafe member path: {name}",
                        )
                    )
                    continue
                if not member.isfile():
                    continue
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                if member.size > LOG_TOTAL_LIMIT_BYTES - total:
                    problems.append(
                        Problem(
                            "layout",
                            relative,
                            f"log archive exceeds {LOG_TOTAL_LIMIT_BYTES} bytes uncompressed",
                        )
                    )
                    return
                data = bytearray()
                # Bound each read by the remaining allowance plus one byte,
                # so an oversized member can never be read fully.
                while chunk := extracted.read(
                    min(LOG_READ_CHUNK_BYTES, LOG_TOTAL_LIMIT_BYTES - total + 1)
                ):
                    data += chunk
                    total += len(chunk)
                    if total > LOG_TOTAL_LIMIT_BYTES:
                        problems.append(
                            Problem(
                                "layout",
                                relative,
                                f"log archive exceeds {LOG_TOTAL_LIMIT_BYTES} bytes uncompressed",
                            )
                        )
                        return
                text = bytes(data).decode("utf-8", errors="replace")
                _report_text_matches(relative, name, text, problems)
    except (tarfile.TarError, OSError) as exc:
        problems.append(Problem("layout", relative, f"cannot read log archive: {exc}"))


def _report_text_matches(
    archive_relative: str, member_name: str, text: str, problems: list[Problem]
) -> None:
    where = f"{archive_relative}#{member_name}"
    matches = scan_text(text)
    if member_name.endswith(".json"):
        try:
            # parse_int bypasses Python's integer-digit limit, so an escaped
            # key such as "access\u005ftoken" still reaches find_secrets even
            # when the member also carries an oversized integer.
            matches += find_secrets(json.loads(text, parse_int=str))
        except RecursionError:
            problems.append(Problem("layout", where, "JSON member is nested too deeply to parse"))
        except ValueError:
            pass
    if matches:
        problems.append(Problem("secret", where, f"possible secret: {', '.join(matches)}"))


def _check_duplicate(
    bundle: Path, manifest: RunManifest | None, against: Path | None, problems: list[Problem]
) -> None:
    if against is None:
        return
    root = against.resolve()
    manifest_file = bundle / "manifest.json"
    manifest_hash: str | None = None
    if manifest_file.is_file():
        try:
            manifest_hash = hashlib.sha256(manifest_file.read_bytes()).hexdigest()
        except OSError as exc:
            problems.append(Problem("layout", "manifest.json", f"cannot read manifest.json: {exc}"))
    if manifest is None:
        return
    for candidate in sorted(root.rglob("submission.json")):
        relative = candidate.parent.relative_to(root)
        other = candidate.parent.resolve()
        if other == bundle:
            continue
        if len(relative.parts) < 2:  # not submissions/<task_id>/<run_id>
            continue
        duplicate_of = relative.as_posix()
        if _read_submission_run_id(other / "submission.json") == manifest.run_id:
            problems.append(Problem("duplicate", bundle.name, f"same run_id as {duplicate_of}"))
        elif (
            manifest_hash is not None
            and (other / "manifest.json").is_file()
            and _sha256_file(other / "manifest.json") == manifest_hash
        ):
            problems.append(
                Problem("duplicate", "manifest.json", f"identical manifest in {duplicate_of}")
            )


def _read_submission_run_id(file: Path) -> str | None:
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    run_id = data.get("run_id") if isinstance(data, dict) else None
    return run_id if isinstance(run_id, str) else None


def _sha256_file(file: Path) -> str | None:
    try:
        return hashlib.sha256(file.read_bytes()).hexdigest()
    except OSError:
        return None
