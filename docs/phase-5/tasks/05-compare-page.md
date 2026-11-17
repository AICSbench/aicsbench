# Task 5: compare page in the browser

**Repository:** `aicsbench/web` · **Branch:** `feat/compare-page` ·
**Depends on:** task 10 · **Commit scope:** `site`

## Why

Static pages answer the common questions. Custom questions ("Claude Code vs
OpenCode on the same model, Laravel only, last 30 days, community included")
need filtering and grouping on demand, without a server.

## Changes

1. Route `src/routes/compare/`. The page shell is prerendered and explains
   that the page needs JavaScript; the controls and results run in the
   browser.
2. DuckDB-WASM from npm (`@duckdb/duckdb-wasm`, exact version in
   `package.json`). Its `.wasm` and worker files are bundled through Vite,
   so they are served from the site itself. Load `data/trials.json` into a
   table `trials` once, on first use.
3. Controls:
   - Task (required; it fixes `task` and `task_version`, newest version
     selected by default).
   - Compare by: harness, harness version, model, harness + model, stack,
     language, framework (and provider once task 9 is merged).
   - Filters: harness, harness version, model, stack, language, framework,
     tooling mode, auth mode, repairs, parallel, verified only (default on),
     exclude rate-limited (default on), submitted after (date).
4. The query lives in one file, `src/lib/compare/query.sql`, with named
   parameters for the filters. It mirrors `comparison.compare_trials`
   exactly. Per group it returns:
   - trial count, passed, success rate and rate-limited count;
   - mean and sample standard deviation of `agent_seconds` over passed,
     non-rate-limited trials;
   - mean `cost_usd` over passed, non-rate-limited trials where it is
     available;
   - the low-confidence flag, set under 3 trials;
   - the same order.

   Before querying, `src/lib/compare/refusals.ts` applies the same refusals
   as `compare_trials`, with the same messages: a mixed comparability key; a
   stack comparison over several harness + model pairs; a model or harness
   comparison over several stacks or several model keys; and so on. The page
   shows the message instead of a table and highlights the filter the user
   should set.
5. Output: the `Leaderboard` and `BarChart` components from task 10, so a
   custom comparison looks like a static one. Values from the data go
   through normal Svelte expressions only; there is no `{@html}`. The state
   lives in the URL query string, so a comparison can be shared and
   reloaded.

## Acceptance criteria

- [ ] A contract test in `ci.yml`'s `contract` job proves the browser and
      the CLI agree. It reads `src/lib/compare/query.sql`, runs it with the
      `duckdb` Python package on the fixture export's `data/trials.json`,
      runs `aicsbench`'s `compare_trials` on the same trials, and asserts
      identical numbers and order for every "compare by" option. The job
      already has Python and the pinned `aicsbench`. This is the only place
      the web app repeats comparison logic, and this test keeps the two in
      step.
- [ ] Vitest covers `refusals.ts` with the same cases as the `compare_trials`
      refusal tests in `aicsbench/aicsbench`, with matching messages.
- [ ] A Playwright test loads the built site from `pnpm preview`, picks
      "Compare by: harness", and sees one row per harness in the fixture.
- [ ] Opening a URL with query parameters restores the same selection.
- [ ] Refusals show the CLI's message, not numbers. The Playwright test
      checks at least a harness comparison across two stacks and a stack
      comparison across two harness + model pairs.
- [ ] No request goes to another origin (covered by task 10's request
      test).

## Out of scope

Server-side queries, accounts, saving comparisons anywhere but the URL.
