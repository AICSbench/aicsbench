# Phase 5 schemas and layouts

Everything here is a contract between tasks. If a task needs to change it,
the change goes into this file in the same PR.

## Submission bundle

One bundle holds exactly one run. It is a folder, committed as plain files
(no archive), so reviewers can read the diff.

```
submissions/<task_id>/<run_id>/
  submission.json            # written by `aicsbench submit`, schema below
  manifest.json              # RunManifest, copied from results/<run_id>/manifest.json, sanitized
  trials/<trial_id>.json     # TrialResult, one per trial in the manifest, sanitized
  logs/<trial_id>.tar.gz     # optional (--with-logs): the trial's logs/ folder
```

`<task_id>` is the run's task, `<run_id>` the run's ID. Both are validated
against `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`.

### `submission.json`

A pydantic model `SubmissionInfo` in `src/ai_benchmark/submission/models.py`,
`extra="forbid"`:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | `int`, must be `1` | Bundle format version. |
| `run_id` | `str` | Must equal `manifest.run_id` and the folder name. |
| `task_id` | `str` | Must equal every trial's `task_id` and the parent folder name. |
| `task_version` | `str` | Must equal every trial's `task_version`. |
| `submitted_at` | `datetime` (UTC, ISO 8601) | When the bundle was made. |
| `submitter` | `{"name": str, "github": str or null}` | Who submits. `github` without `@`. |
| `notes` | `str` (≤ 2000 chars) | Free text: hardware, anything unusual. |
| `platform_version` | `str` | `manifest.platform_version`. |
| `platform_git_commit` | `str or null` | `manifest.platform_git_commit`. |
| `acceptance_suite_sha256` | `str` (64 hex) | Hash of the task's acceptance suite, algorithm below. |
| `suite_hash_source` | `"run"` or `"submit"` | `run` when the manifest recorded it (task 2 adds that), `submit` when it was computed at submit time from the local catalog. |
| `logs_included` | `bool` | True when `logs/` is present. |
| `files` | `dict[str, str]` | Relative path → SHA-256 (hex) of every other file in the bundle, `submission.json` excluded. |

### Acceptance suite hash

`suite_sha256(task)`: walk the suite directory (`task.acceptance_tests.suite`,
resolved against the task file), skip `__pycache__/`, `.pytest_cache/` and
`*.pyc`, sort the remaining files by their POSIX relative path, and feed
SHA-256 with, per file, `relative_path + "\0" + file_bytes + "\0"`. A task
without a suite hashes to the SHA-256 of the empty string.

Task 2 also records it at run time as `RunManifest.acceptance_suite_sha256:
str | None = None` (optional, so older manifests still load).

### Sanitizing

`aicsbench submit` writes sanitized copies; it never edits `results/`.

- `TrialResult.log_files`: each value becomes the path inside the bundle
  (`logs/<trial_id>.tar.gz#<original file name>`) when logs are included,
  otherwise the key is kept with value `""`. No absolute host paths survive.
- `RunManifest.host`: keep only `platform`, `python`, `machine`.
- `TrialResult.raw`: kept as is (it is provider output, already secret-free),
  but included in the secret scan.
- Secret scan (also run by the validator), defined in
  `src/ai_benchmark/submission/secrets.py`. It fails when either:
  - a string value, or a JSON key, matches a pattern in `SECRET_PATTERNS`:
    `sk-[A-Za-z0-9_-]{20,}`, `sk-ant-[A-Za-z0-9_-]{20,}`, `gh[pousr]_[A-Za-z0-9]{30,}`,
    `xox[baprs]-[A-Za-z0-9-]{10,}`, `-----BEGIN [A-Z ]*PRIVATE KEY-----`,
    `eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.` (a JWT),
    `(?i)(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|OPENAI_API_KEY|CODEX_API_KEY)=\S+`;
  - or a JSON key, compared case-insensitively, is in `SECRET_KEYS`
    (`access_token`, `refresh_token`, `id_token`, `api_key`, `apikey`,
    `authorization`, `password`, `client_secret`, `secret`) and its value is
    a non-empty string. The key's presence alone is fine; its value is not.
- Log archives: `submit` scans every log file with `SECRET_PATTERNS` (as
  plain text) **before** compressing it and refuses the bundle on a match.
  The validator opens each `logs/*.tar.gz` (read-only, regular files only,
  no member path with `..` or a leading `/`, at most 50 MB uncompressed in
  total), decodes each member as UTF-8 with replacement, and applies the
  same patterns.

## Validation rules

`validate_bundle(path, catalog, *, against: Path | None = None,
check_catalog: bool = True) -> list[Problem]` in
`src/ai_benchmark/submission/validate.py`; `Problem` has `code`, `path`,
`message`. An empty list means valid. The CLI command
`aicsbench validate-submission PATH... [--against DIR] [--no-catalog-check]`
prints problems and exits 1 if any.

Two modes:

- **Admission** (`check_catalog=True`, the default): used by `submit` and by
  the data repository's CI for new bundles. A new bundle must match a task
  version the current catalog ships.
- **Rebuild** (`check_catalog=False`): used by the site build for bundles
  already merged. The `task` and `suite` rules are skipped, so results for a
  task version the catalog no longer ships stay on the site, shown under
  their own version. Every other rule still runs.

| Code | Rule |
| --- | --- |
| `layout` | Folder matches `submissions/<task_id>/<run_id>/`, required files present, no other files. |
| `schema` | `submission.json`, `manifest.json` and every trial parse with their pydantic models. |
| `hash` | Every file's SHA-256 matches `files`; no file missing from or extra to `files`. |
| `ids` | `run_id`, `task_id`, `task_version` agree across folder names, `submission.json`, manifest and trials; the manifest's `trial_ids` equal the trial files. |
| `task` | (admission only) The task ID and version exist in the catalog the validator runs with. |
| `suite` | When `suite_hash_source` is `run`, `acceptance_suite_sha256` equals `manifest.acceptance_suite_sha256`. In admission mode it must also equal the hash of the catalog task's suite. |
| `comparable` | All trials share one `comparability_key()`. |
| `harness` | Every trial has a non-empty `agent.name` and `agent.version`. |
| `status` | No trial is `pending` or `running`. |
| `secret` | The secret scan above finds nothing in any JSON file or log archive member. |
| `duplicate` | (only with `--against DIR`) no bundle under `DIR`, other than the one being validated (compared by resolved path), has the same `run_id` or the same SHA-256 of `manifest.json`. CI passes a checkout of the base branch as `DIR`, so the new bundle is never compared with itself. |

## Site data file

`aicsbench site build` writes `data/trials.json`: a JSON array with one
object per trial of every accepted submission. Each object is
`reporting.trial_row(trial)` (the existing export row) plus these keys, all
added by task 1 to `trial_row` itself so CSV/JSONL exports get them too:

| Key | Source |
| --- | --- |
| `harness` | `agent.name` (same value as the existing `agent` key, kept for compatibility) |
| `harness_version` | `agent.version` |
| `requested_model` | `agent.requested_model` |
| `reasoning_effort` | `agent.reasoning_effort` |

and these keys added only in the site file:

| Key | Source |
| --- | --- |
| `verified` | run ID listed in `verified-runs.txt` |
| `logs_available` | `submission.json.logs_included` |
| `submitted_at` | `submission.json.submitted_at` |
| `submitter` | `submission.json.submitter.github` or `name` |

`data/meta.json` holds `{"built_at", "platform_version", "runs", "trials",
"tasks": [{"id", "version"}]}`.

## Site layout

All pages are rendered at build time and readable without JavaScript.
Every path ends in `/` and is written as `<path>/index.html`.

| Path | Content |
| --- | --- |
| `/` | What the benchmark measures, latest runs, leaderboard per task (pass rate, then mean agent time) for verified runs |
| `/tasks/<task>/` | Task description, its versions, a harness + model leaderboard per stack, and a stack leaderboard per harness + model |
| `/tasks/<task>/harnesses/<harness>/` | That harness on that task: by model (per stack), by stack (per model), by harness version (per model and stack) |
| `/tasks/<task>/models/<model>/` | That model: by harness (per stack), by stack (per harness) |
| `/tasks/<task>/stacks/<stack>/` | That stack: by harness + model |
| `/runs/<run_id>/` | Run metadata, every trial row, link to the raw bundle on GitHub |
| `/compare/` | The interactive compare page (task 5); static fallback text explains it needs JavaScript |
| `/methodology/` | How trials run, what each metric means, comparability rules, verified vs community |
| `/sitemap.xml`, `/robots.txt` | All pages above; `robots.txt` allows all and points at the sitemap |

Slugs: lower case, characters outside `[a-z0-9._-]` replaced by `-`. When
two different values of the same dimension would get the same slug (for
example `Foo/Bar` and `Foo-Bar`), every value in that colliding set gets
`-<first 8 hex characters of SHA-256 of the original value>` appended, so
paths stay distinct and stable across builds. The build asserts no two pages
share an output path.
Every page has a unique `<title>`, a `<meta name="description">`, a
canonical URL from `--base-url`, Open Graph tags, and JSON-LD
(`Dataset` on task pages, `WebSite` on `/`). Charts are inline SVG generated
in Python, never images or JavaScript-only.

Leaderboard rows use `comparison.compare_trials`, which refuses a
comparison that leaves another dimension mixed (for example comparing stacks
across several harness + model pairs). Pages therefore split each leaderboard
by the dimensions it must hold fixed, as the "per ..." wording above says.
Groups with fewer than 3 trials are shown but marked low confidence, exactly
like the CLI.
