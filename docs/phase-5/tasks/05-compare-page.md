# Task 5: compare page in the browser

**Branch:** `feat/phase5-compare-page` · **Depends on:** task 4 ·
**Commit scope:** `site`

## Why

Static pages answer the common questions. Custom questions ("Claude Code vs
OpenCode on the same model, Laravel only, last 30 days, community included")
need filtering and grouping on demand, without a server.

## Changes

1. `/compare/index.html` (template from task 4) plus
   `src/ai_benchmark/site/static/compare.js`, a plain ES module.
2. Load DuckDB-WASM from `https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@<exact version>/`
   (pin an exact version at least two weeks old), register
   `data/trials.json`, and create a view `trials`.
3. Controls:
   - Task (required; it fixes `task` and `task_version`, newest version
     selected by default).
   - Compare by: harness, harness version, model, harness + model, stack,
     language, framework.
   - Filters: harness, harness version, model, stack, language, framework,
     tooling mode, auth mode, repairs, parallel, verified only (default on),
     exclude rate-limited (default on), submitted after (date).
4. The query mirrors `comparison.compare_trials` exactly: per group, trial
   count, passed, success rate, rate-limited count, mean and sample standard
   deviation of `agent_seconds` over passed, non-rate-limited trials, mean
   `cost_usd` over passed, non-rate-limited trials where it is available, and
   the low-confidence flag under 3 trials. Before querying, apply the same
   refusals as `compare_trials` (mixed comparability key; a stack comparison
   over several harness + model pairs; a model or harness comparison over
   several stacks; and so on) and show the same message instead of a table,
   with the filter the user should set highlighted.
5. Output: a table and an SVG bar chart, both updated on change; the state
   lives in the URL query string so a comparison can be shared and reloaded.
6. Without JavaScript the page shows a short explanation and links to the
   static leaderboards.

## Acceptance criteria

- [ ] A Python test runs the same comparison through `compare_trials` and
      through the SQL string the page uses (executed with the `duckdb`
      Python package installed only in the test extra) on the fixture data,
      and asserts identical numbers.
- [ ] A Playwright test (marked, skipped when Playwright is unavailable)
      loads the built site from a local HTTP server, picks
      "Compare by: harness", and sees one row per harness in the fixture.
- [ ] Opening a URL with query parameters restores the same selection.
- [ ] Refusals show the CLI's message, not numbers: the Playwright test
      checks at least a harness comparison across two stacks and a stack
      comparison across two harness + model pairs.
- [ ] No request goes anywhere except the site itself and the pinned
      jsDelivr URL.

## Out of scope

Server-side queries, accounts, saving comparisons anywhere but the URL.
