# Site export

`aicsbench site export` turns a checkout of `aicsbench/data` into everything
the public website needs. `aicsbench/web` only renders it: every number,
grouping and page path is computed here with `comparison.py`, so the site
shows exactly what the CLI shows (decision D3). The file layout and keys are
specified in [schemas.md](schemas.md#site-export).

## Command

```
aicsbench [--tasks-dir DIR] site export --data DIR --out DIR --base-url URL
```

- `--data`: a checkout of `aicsbench/data`. Every bundle under
  `submissions/<task_id>/<run_id>/` is validated in rebuild mode
  (`validate_bundle(..., check_catalog=False)`); a bundle that still fails is
  skipped with a warning naming its problems, and the export continues. A run
  is verified when its ID is a line of `verified-runs.txt` (`#` starts a
  comment).
- `--out`: emptied first, then written. The whole export is built in memory
  first, so a failure (for example a page path collision) writes nothing.
- `--base-url`: stored in `export.json` for canonical links and the sitemap.
- Task names and descriptions come from the catalog (`--tasks-dir`). A task
  version the catalog no longer ships still appears under its own version; a
  task missing from the catalog is listed by its ID (`name: null` in
  `data/meta.json`).

`export.json`'s `data_commit` is `git rev-parse HEAD` of `--data`, but only
when `--data` is itself the root of a repository; otherwise it is `null`.

## Pages and leaderboards

`pages.json` lists every page of the [site layout](schemas.md#site-layout).
Each leaderboard section is exactly `compare_trials` on one set of trials
that share a comparability key, one acceptance suite hash, verified or
community status, and the page's "per ..." axes. A task version with several
suites, tooling modes or auth modes gets one section for each, and the
heading names the difference (`suite 1a2b3c4d`). Verified sections come
first; community sections are labelled "community results".

`/leaderboard/` uses `comparison.compare_matrix`: rows are harness + model,
one column per task at its newest verified version, one section per stack.
A column that `compare_trials` refuses, or that mixes acceptance suites,
carries the refusal and counts towards no row's overall. The overall is the
unweighted mean of the row's per-column success rates, the coverage and the
mean rank; rows missing a column follow the complete rows. The same matrix is
available in the CLI: `aicsbench compare --by harness_model --tasks t1,t2`.

Page path slugs depend only on their value (see
[schemas.md](schemas.md#site-layout)). Two values that would share a path
fail the export with an error naming both.

Submitted strings (submitter, notes, model, harness, stack) are copied
unchanged. Escaping is the web app's job.

## Export schema version

`export.json` carries `export_schema: 1`. Removing or renaming a key bumps it;
adding a key does not. `aicsbench/web` refuses an export whose
`export_schema` it does not support.

`schema/*.schema.json` (JSON Schema draft 2020-12) are generated from the
pydantic models in `src/ai_benchmark/site/models.py` that write the export,
so they cannot drift from it. Every model forbids extra keys.

## How `aicsbench/web` consumes it

1. The data repository runs the export on every merge and commits it to its
   `export` branch.
2. The web build reads that branch, checks `export_schema`, validates every
   file against `schema/`, generates its TypeScript types from the same
   schemas, and renders one template per page `kind`.
3. The compare page loads `data/trials.json` into DuckDB-WASM.

## Fixture

`tests/fixtures/site-export/` is an export of `tests/fixtures/site-data/`
(two bundles: one verified, one community, two harnesses, two stacks; the
verified run has Claude Code + Opus on Anthropic and Codex + GPT on OpenAI on
one stack, so its harness + model leaderboards rank two providers' packages;
with `<script>alert(1)</script>` in a submitter name and notes). `aicsbench/web`
copies it for its own tests, so changing it changes that contract.
Regenerate both folders with:

```
python -m tests.regen_site_export
```

`tests/test_site_export.py` fails when the committed fixture differs from a
fresh regeneration, and validates it against its own schemas.
