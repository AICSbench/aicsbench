# Task 4: static site generator

**Branch:** `feat/phase5-site-generator` · **Depends on:** task 1
(task 2 for loading bundles; until it is merged, load bundles with plain
pydantic parsing and switch to `submission.models` after) ·
**Commit scope:** `site`, `cli`, `build`

## Why

Search engines and people without JavaScript must see the results as
ordinary pages, with the same numbers the CLI computes.

## Command

```
aicsbench [--tasks-dir DIR] site build --data DIR --out DIR --base-url URL
```

- `--data`: a checkout of the data repository (reads `submissions/`,
  `verified-runs.txt`).
- `--out`: output folder, emptied first.
- `--base-url`: absolute URL the site is served from (canonical links,
  sitemap, Open Graph).
- Task names and descriptions come from the normal task catalog
  (global `--tasks-dir`); a task in the data but not in the catalog is shown
  by ID only.

## Changes

1. Optional extra in `pyproject.toml`: `site = ["jinja2>=3.1"]`. The
   command prints a clear install hint if Jinja2 is missing.
2. `src/ai_benchmark/site/`:
   - `load.py`: read every bundle under `submissions/`, skip (with a
     warning) any that `validate_bundle` rejects, attach `verified`,
     `logs_available`, `submitted_at`, `submitter`.
   - `pages.py`: compute every page in
     [schemas.md](../schemas.md#site-layout) with `comparison.compare_trials`.
     Comparisons only ever group trials with one `comparability_key`; a task
     page with several keys (for example two task versions) shows one section
     per key, newest task version first.
   - `charts.py`: inline SVG bar charts (pass rate, mean agent time with a
     standard deviation whisker) from comparison rows. Colours via CSS
     custom properties so light and dark themes work.
   - `templates/`: Jinja2 templates (`base.html` with the SEO head, one per
     page type) and one `site.css`. No external fonts or scripts on static
     pages.
   - `build.py`: write pages, `data/trials.json`, `data/meta.json`,
     `sitemap.xml`, `robots.txt`, and copy each accepted bundle's
     `submission.json` and `manifest.json` under `/runs/<run_id>/` for
     download.
3. Leaderboards default to verified runs. Each page with community runs
   shows them in a separate, clearly labelled table.
4. Unavailable metrics render as "unavailable" (with the metric's note as a
   `title`), never `0` or blank.
5. Docs: `docs/phase-5/site.md` describing how to build and preview locally
   (`python -m http.server -d OUT`).

## Acceptance criteria

- [ ] Building from a fixture data folder (two bundles: one verified, one
      community; two harnesses; two stacks) produces every page type in the
      layout table, and the test asserts the presence of a few known values
      (a pass rate, a harness name, a run ID) in the HTML.
- [ ] Every page has a unique `<title>`, a meta description, a canonical
      link starting with `--base-url`, and valid JSON-LD (parse it in the
      test).
- [ ] `sitemap.xml` lists every page; `robots.txt` references it.
- [ ] The numbers on a leaderboard equal `aicsbench compare` for the same
      trials (test compares both).
- [ ] An invalid bundle in the data folder is skipped with a warning and the
      build still succeeds.
- [ ] Pages render with JavaScript disabled (no content depends on JS).
- [ ] The core install (`pip install -e .`) does not pull Jinja2.

## Out of scope

The interactive compare page (task 5). Deployment (task 6).
