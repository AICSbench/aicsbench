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
- Host paths: in every string of the manifest and the trials (including
  `raw` and `error`), `submit` replaces the run's results folder, its
  workspace folder and the user's home folder with `<results>`,
  `<workspace>` and `~`. `raw` is otherwise kept as is and included in the
  secret scan. The validator cannot know another machine's paths, so this
  is `submit`'s job, and its test greps the bundle for the test's temp
  path.
- Secret scan (also run by the validator), defined in
  `src/ai_benchmark/submission/secrets.py`. It fails when either:
  - a string value, or a JSON key, matches a pattern in `SECRET_PATTERNS`:
    `sk-[A-Za-z0-9_-]{20,}`, `sk-ant-[A-Za-z0-9_-]{20,}`, `gh[pousr]_[A-Za-z0-9]{30,}`,
    `xox[baprs]-[A-Za-z0-9-]{10,}`, `-----BEGIN [A-Z ]*PRIVATE KEY-----`,
    `eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.` (a JWT),
    `(?i)(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|OPENAI_API_KEY|CODEX_API_KEY)=\S+`,
    `(A3T[A-Z0-9]|AKIA|ASIA)[A-Z0-9]{16}` (an AWS access key ID),
    `\b[A-Z0-9_]*(SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|ACCESS_KEY)[A-Z0-9_]*=["']?(?=[^\s"']*[A-Za-z])[^\s"']{8,}`
    (an env-style assignment of an upper-case, secret-named variable to a
    value with at least one letter, which covers `AWS_SECRET_ACCESS_KEY=...`
    but not `MAX_TOKENS=200000`);
  - or a JSON key, compared case-insensitively, is in `SECRET_KEYS`
    (`access_token`, `refresh_token`, `id_token`, `api_key`, `apikey`,
    `authorization`, `password`, `client_secret`, `secret`) and its value is
    a non-empty string. The key's presence alone is fine; its value is not.
- Log archives: `submit` scans every log file **before** compressing it and
  refuses the bundle on a match. Each file is scanned as plain text with
  `SECRET_PATTERNS` plus one more pattern for structured secrets:
  `(?i)["']?(access_token|refresh_token|id_token|api_key|apikey|authorization|password|client_secret|secret)["']?\s*[:=]\s*["'][^"']{4,}["']`
  (a quoted value only, so `"secret": false` is fine).
  Every line that parses as JSON (JSONL logs) and every `*.json` file is
  also checked with `find_secrets`, so `SECRET_KEYS` apply there too.
  The validator opens each `logs/*.tar.gz` (read-only, regular files only,
  no member path with `..` or a leading `/`, at most 50 MB uncompressed in
  total), decodes each member as UTF-8 with replacement, and applies the
  same checks.
- The scan is a safety net, not a guarantee. Logs are opt-in
  (`--with-logs`), and the PR template asks the submitter to read them.

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
- **Rebuild** (`check_catalog=False`): used by `aicsbench site export` for bundles
  already merged. The `task` rule and the catalog part of the `suite` rule
  are skipped, so results for a task version the catalog no longer ships
  stay on the site, shown under their own version. The `suite` manifest
  check and every other rule still run.

| Code | Rule |
| --- | --- |
| `layout` | Folder matches `submissions/<task_id>/<run_id>/`, required files present, no other files. |
| `schema` | `submission.json`, `manifest.json` and every trial parse with their pydantic models. |
| `hash` | Every file's SHA-256 matches `files`; no file missing from or extra to `files`. |
| `ids` | `run_id`, `task_id`, `task_version` agree across folder names, `submission.json`, manifest and trials; the manifest's `trial_ids` equal the trial files. |
| `task` | (admission only) The task ID and version exist in the catalog the validator runs with. |
| `suite` | A task version's acceptance suite never changes: editing a suite requires a new task version (CONTRIBUTING.md says so, and task 2 adds a test that the shipped tasks' hashes match a committed list). When `suite_hash_source` is `run`, `acceptance_suite_sha256` equals `manifest.acceptance_suite_sha256`. In admission mode it must also equal the hash of the catalog task's suite. |
| `comparable` | All trials share one `comparability_key()`. |
| `harness` | Every trial has a non-empty `agent.name` and `agent.version`. |
| `status` | No trial is `pending` or `running`. |
| `secret` | The secret scan above finds nothing in any JSON file or log archive member. |
| `duplicate` | (only with `--against DIR`) no bundle under `DIR`, other than the one being validated (compared by resolved path), has the same `run_id` or the same SHA-256 of `manifest.json`. CI passes a checkout of the base branch as `DIR`, so the new bundle is never compared with itself. |

## Site export

`aicsbench site export --data DIR --out DIR --base-url URL` (task 4, this
repository) reads a checkout of `aicsbench/data` and writes everything the
website needs. `aicsbench/web` renders it and computes nothing. The export
is committed to the data repository's `export` branch on every merge.

```
<out>/
  export.json          # {"export_schema": 1, "built_at", "platform_version",
                       #  "platform_git_commit", "data_commit", "base_url"}
  data/trials.json     # one row per trial, see below
  data/meta.json       # {"runs", "trials", "tasks": [{"id", "version", "name", "description"}]}
  pages.json           # every static page, see below
  schema/export.schema.json   # JSON Schema (draft 2020-12) for export.json
  schema/trials.schema.json   # ... for data/trials.json
  schema/meta.schema.json     # ... for data/meta.json
  schema/pages.schema.json    # ... for pages.json
  runs/<run_id>/submission.json
  runs/<run_id>/manifest.json
```

The JSON Schemas are generated from the pydantic models that write the
export (`model_json_schema()`), so they cannot drift from it. They are the
contract with `aicsbench/web`: it generates its TypeScript types from them
and validates every export against them before building.

`export_schema` starts at 1. A change that removes or renames a key bumps
it. `aicsbench/web` refuses an export whose `export_schema` it does not
support and fails the build with a message naming both numbers. Adding a key
does not bump it.

### `data/trials.json`

A JSON array with one object per trial of every accepted submission. Each
object is `reporting.trial_row(trial)` (the existing export row) plus these
keys, all added to `trial_row` itself so CSV/JSONL exports get them too:

| Key | Source |
| --- | --- |
| `harness` | `agent.name` (same value as the existing `agent` key, kept for compatibility); task 1 |
| `harness_version` | `agent.version`; task 1 |
| `requested_model` | `agent.requested_model`; task 1 |
| `reasoning_effort` | `agent.reasoning_effort`; task 1 |
| `language_version_installed` | `environment.installed_versions["language"]` (task 7; the existing `language_version` key stays the version the stack declares) |
| `framework_version_installed` | `environment.installed_versions["framework"]` (task 7; the existing `framework_version` key stays the version the stack declares) |

and these keys added only in the export:

| Key | Source |
| --- | --- |
| `verified` | run ID listed in `verified-runs.txt` |
| `logs_available` | `submission.json.logs_included` |
| `suite_sha256` | `submission.json.acceptance_suite_sha256` |
| `submitted_at` | `submission.json.submitted_at` |
| `submitter` | `submission.json.submitter.github` or `name` |

### `pages.json`

```json
{"pages": [
  {"path": "/tasks/url-shortener-api/",
   "kind": "task",
   "title": "...", "description": "...",
   "params": {"task": "url-shortener-api"},
   "sections": [
     {"heading": "...",
      "fixed": {"task_version": "1.0.0", "stack": "fastapi-latest"},
      "by": "harness_model",
      "verified_only": true,
      "rows": [ComparisonRow, ...],
      "refused": null}
   ],
   "links": [{"path": "...", "label": "..."}]}
]}
```

- `kind` is one of `home`, `task`, `harness`, `model`, `stack`, `run`,
  `compare`, `methodology`. One template per kind in `aicsbench/web`.
- `rows` are `dataclasses.asdict(comparison.ComparisonRow)` plus
  `success_rate` (`passed / trials`), in the order `compare_trials` returns
  them. A `null` mean (no timed or costed trials) renders as "unavailable",
  never `0`.
- `refused` is the `compare_trials` error message when a section cannot be
  compared. It is shown instead of a table. Normally the "per ..." splits
  below prevent it.
- `title` and `description` are plain text. All escaping is the web app's
  job.
- Every page with community runs has a second section for them with
  `verified_only: false`, labelled as community results.

## Site layout

The export lists these pages (`pages.json`), and `aicsbench/web` renders
each one at build time, readable without JavaScript.
Every path ends in `/` and is written as `<path>/index.html`.

| Path | Content |
| --- | --- |
| `/` | What the benchmark measures, latest runs, leaderboard per task for verified runs, ordered as `compare_trials` orders it (lowest mean agent time over passed trials first) with the success rate shown beside it |
| `/tasks/<task>/` | Task description, its versions, a harness + model leaderboard per stack, and a stack leaderboard per harness + model |
| `/tasks/<task>/harnesses/<harness>/` | That harness on that task: by model (per stack), by stack (per model), by harness version (per model and stack) |
| `/tasks/<task>/models/<model>/` | That model: by harness (per stack), by stack (per harness) |
| `/tasks/<task>/stacks/<stack>/` | That stack: by harness + model |
| `/runs/<run_id>/` | Run metadata, every trial row, link to the raw bundle on GitHub |
| `/compare/` | The interactive compare page (task 5, in `aicsbench/web`); static fallback text explains it needs JavaScript |
| `/methodology/` | How trials run, what each metric means, comparability rules, verified vs community |
| `/sitemap.xml`, `/robots.txt` | All pages above; `robots.txt` allows all and points at the sitemap |

Slugs depend only on the value, never on which other values exist, so a
page path never changes when new data arrives. The base slug is the value in
lower case with characters outside `[a-z0-9._-]` replaced by `-`. If the base
slug equals the original value (for example `gpt-6-luna`), does not start
with `.` and does not end in `-` plus 8 hex characters, it is the slug. So
`.` and `..` never become path segments of their own. Otherwise the slug is the base
slug plus `-<first 8 hex characters of SHA-256 of the original value>`, so
`Foo/Bar` and `Foo-Bar` get distinct paths, and a value that already looks
like a suffixed slug (`foo-bar-c84b3c5b`) is suffixed again instead of taking
another value's path. The export fails if two pages would share an output
path, naming both values. The 8-character hash can still collide for crafted
values; such a collision can only come from a new submission, and the data
repository's `validate` check runs the export, so that PR fails and cannot be
merged. Pages already published never change path.
Every rendered page has a unique `<title>`, a `<meta name="description">`, a
canonical URL from the export's `base_url`, Open Graph tags, and JSON-LD
(`Dataset` on task pages, `WebSite` on `/`). Charts are inline SVG drawn by
the web app from `rows` and prerendered, never images or JavaScript-only.

Leaderboard rows use `comparison.compare_trials`, which refuses a
comparison that leaves another dimension mixed (for example comparing stacks
across several harness + model pairs). Pages therefore split each leaderboard
by the dimensions it must hold fixed, as the "per ..." wording above says.
Groups with fewer than 3 trials are shown but marked low confidence, exactly
like the CLI.
