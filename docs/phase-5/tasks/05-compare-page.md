# Task 5: compare page in the browser

**Repository:** `aicsbench/web` · **Branch:** `feat/compare-page` ·
**Depends on:** tasks 1 (harness dimensions) and 10 ·
**Commit scope:** `site`

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
     language, framework (and provider once task 9 is merged), or a
     combination of up to 3 of them (task 1 item 5).
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
   comparison over several stacks or several model keys; a harness or
   harness version comparison over several providers, or a harness + model
   row over several providers (rows may differ in provider; task 9 item 5);
   an unknown provider in any of these; a selection that
   mixes `suite_sha256` values ("comparison mixes acceptance suites; pick
   one suite", with a `suite` filter listing the hashes); and so on. The page
   shows the message instead of a table and highlights the filter the user
   should set.
5. Output: the `Leaderboard` and `BarChart` components from task 10, so a
   custom comparison looks like a static one. Values from the data go
   through normal Svelte expressions only; there is no `{@html}`. The state
   lives in the URL query string, so a comparison can be shared and
   reloaded.
6. **Matrix view.** With `view=matrix`, the rows are the "compare by"
   groups and the columns are several tasks, for example:

   | | url-shortener-api | todo-api | auth-api | Overall |
   | --- | --- | --- | --- | --- |
   | claude + claude-opus-5-5 | 95% · 210 s | 90% · 340 s | 85% · 400 s | 90% · 3/3 |
   | codex + gpt-6-luna | 100% · 180 s | 80% · 300 s | (no data) | 90% · 2/3 |

   - **Each column is its own comparison.** A column is one task at one
     version (newest by default, or pinned with `task=<id>@<version>`). Its
     cells are exactly the single-task comparison with the same `by` and
     filters, so every refusal still applies per column (one stack for a
     model comparison, one provider for a harness comparison, one provider
     per row for a harness + model comparison, one suite, and
     so on). A refused column shows the message in its header and has no
     cells. Rows never mix task versions or suites inside one cell.
   - **Cell:** success rate and mean agent time over passed trials, with the
     low-confidence mark under 3 trials, linking to the single-task compare
     URL for that column.
   - **Overall:** the unweighted mean of the row's per-task success rates
     (each task counts once, whatever its trial count), shown with coverage
     ("3/3"). Agent times are not averaged across tasks, because tasks take
     very different times; instead the overall shows the row's mean rank
     across its columns (rank 1 = best in that column, by the single-task
     order). Rows with full coverage are ranked first, by overall success
     rate, then by mean rank; rows missing a task follow, marked
     "incomplete", and are never ranked above a complete row. A refused
     column is left out of every row's overall and of the coverage count.
   - The query is the same `query.sql`, run once per column. The Overall is
     computed in `src/lib/compare/matrix.ts`, mirroring
     `comparison.compare_matrix` (task 4).
7. **URL format.** `/compare/?<params>`. Values are the raw data values
   (`gpt-6-luna`, `openai/gpt-6-luna`), URL-encoded, not page slugs. A
   parameter that takes several values uses commas. Unknown parameters are
   ignored. When the page writes the URL, it writes parameters in the order
   below and leaves out defaults.

   | Param | Meaning | Default |
   | --- | --- | --- |
   | `view` | `matrix` for the matrix view (item 6) | single task |
   | `task` | task ID (required); in the matrix view several, comma-separated, each optionally `<id>@<version>` | none: the page asks for it |
   | `v` | task version (single-task view only) | newest |
   | `by` | one dimension, or up to 3 comma-separated (task 1 item 5): `harness`, `harness_version`, `model`, `harness_model`, `stack`, `language`, `framework`, `provider` | `harness,model` |
   | `harness`, `harness_version`, `model`, `stack`, `language`, `framework`, `provider` | filters, comma-separated | all |
   | `mode`, `auth` | tooling mode, auth mode | all |
   | `repairs`, `parallel` | repair budget, parallelism | all |
   | `verified` | `1` verified only, `0` include community | `1` |
   | `rl` | `1` include rate-limited trials | excluded |
   | `since` | submitted on or after, `YYYY-MM-DD` | none |
   | `suite` | `suite_sha256` (full or a unique prefix of at least 8 hex) | the only suite, if there is one |

   For example, three harnesses on one model and stack:
   `/compare/?task=url-shortener-api&by=harness&model=gpt-6-luna&stack=fastapi-latest`.
   `model` matches `model_key` (task 1), so `gpt-6-luna` also matches
   OpenCode's `openai/gpt-6-luna`.

   Matrix examples:
   - models across three tasks, one harness and stack:
     `/compare/?view=matrix&task=url-shortener-api,todo-api,auth-api&by=model&harness=claude&stack=fastapi-latest`
   - stack + model rows (one harness), so Laravel + Opus and FastAPI + Opus
     are separate rows:
     `/compare/?view=matrix&task=url-shortener-api,todo-api&by=stack,model&harness=claude`
   - language + model rows, pooling each language's stacks:
     `/compare/?view=matrix&task=url-shortener-api,todo-api&by=language,model&harness=claude`
   - harness + model pairs on one stack, one task pinned to a version:
     `/compare/?view=matrix&task=url-shortener-api@1.0.0,todo-api&stack=laravel13-boost`

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
- [ ] A fixture export with two `suite_sha256` values for one task version
      is refused until a `suite` filter selects one.
- [ ] The contract test also runs the matrix view: `query.sql` per column
      plus `matrix.ts` against `compare_matrix` on the fixture, for two
      tasks, with identical cells, overall values and row order.
- [ ] A matrix with one refused column shows the refusal in that column's
      header and leaves it out of every overall.
- [ ] A Playwright test loads the built site from `pnpm preview`, picks
      "Compare by: harness", and sees one row per harness in the fixture.
- [ ] Opening a URL with query parameters restores the same selection;
      changing a control writes the canonical URL (fixed order, defaults
      left out), tested with the example URL above.
- [ ] Refusals show the CLI's message, not numbers. The Playwright test
      checks at least a harness comparison across two stacks and a stack
      comparison across two harness + model pairs.
- [ ] No request goes to another origin (covered by task 10's request
      test).

## Out of scope

Server-side queries, accounts, saving comparisons anywhere but the URL.
