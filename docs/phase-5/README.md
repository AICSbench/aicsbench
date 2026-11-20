# Phase 5: public results through pull requests

Phase 5 makes benchmark results public. Anyone can submit a run as a pull
request to a data repository, and every merge there rebuilds a static,
search-engine-friendly website plus a data file that the browser queries
for filtering and custom comparisons. No application server, no accounts.

## Repositories

All three live in the GitHub organization `aicsbench` (decision D9):

| Repository | Holds | Phase 5 tasks |
| --- | --- | --- |
| `aicsbench/aicsbench` | This CLI and platform (formerly `dyaskur/ai-coding-stack-bench`). All computation: validation, comparisons, the site export. | 1, 2, 3, 4, 7, 8, 9 |
| `aicsbench/data` | Submission bundles, `verified-runs.txt`, the PR checks, and the published export on its `export` branch. | 6 |
| `aicsbench/web` | The SvelteKit site (prerendered pages, the DuckDB-WASM compare page) and the GitHub Pages deploy. Renders the export, computes nothing. | 10, 5 |

Steps only the maintainer can do (creating the organization, transferring
this repository, installing apps, the dispatch token, branch protection)
are listed in [setup.md](setup.md).

This folder is written so that an AI coding agent (or a person) can pick up
one task and finish it without further context. Read this page first, then
[decisions.md](decisions.md), [schemas.md](schemas.md) and the one task file
you are working on.

## Goal

1. `aicsbench submit --run RUN_ID` packs a finished run into a bundle and
   opens a PR on the data repository.
2. CI on the data repository validates the bundle with the same code
   (`aicsbench validate-submission`).
3. A merge to the data repository's `main` runs `aicsbench site export`,
   commits the export to its `export` branch, and notifies `aicsbench/web`,
   which renders the static HTML and deploys it.
4. Visitors compare results by **harness** (the agent CLI: Claude Code,
   Codex, OpenCode, ...), harness version, model, harness + model, stack,
   language, framework and task, with filters, across runs.

```
run ──► aicsbench submit ──► PR on aicsbench/data ──► validate + guard
                                                          │ fail: fix, push again
                                                          ▼ pass
                                                    review + merge
                                                          │
                     aicsbench site export ──► `export` branch ──► repository_dispatch
                                                                          │
                                       GitHub Pages ◄── render ◄── aicsbench/web
```

## Out of scope

User accounts, an upload API, a server-side database, comments, and any
change to how trials are executed. Revisit when submission volume needs it.

## Rules that do not change

- Results that differ in task/version, tooling mode, auth mode, repair
  budget or parallelism are never compared (`TrialResult.comparability_key`).
- A metric the agent did not report is **unavailable**, never zero, in the
  bundle, the data file and every page.
- Secrets never enter a bundle. Results never store them; the validator
  scans anyway.

## Tasks, in order

Each task is one pull request. Start a task only when the tasks it depends
on are merged into `main`.

Track progress in the [Phase 5 issue](https://github.com/AICSbench/aicsbench/issues/10)
and the individual task issues. GitHub Issues are the source of truth for status.

| # | Task | Depends on | Repository | Issue |
| --- | --- | --- | --- | --- |
| 1 | [Harness fields and dimensions](tasks/01-harness-dimensions.md) | none | `aicsbench/aicsbench` | [#11](https://github.com/AICSbench/aicsbench/issues/11) |
| 2 | [Submission bundle and validator](tasks/02-bundle-and-validator.md) | 1 | `aicsbench/aicsbench` | [#12](https://github.com/AICSbench/aicsbench/issues/12) |
| 3 | [`aicsbench submit`](tasks/03-submit-command.md) | 2 | `aicsbench/aicsbench` | [#13](https://github.com/AICSbench/aicsbench/issues/13) |
| 4 | [`aicsbench site export`](tasks/04-site-export.md) | 1, 2 | `aicsbench/aicsbench` | [#14](https://github.com/AICSbench/aicsbench/issues/14) |
| 7 | [Installed language and framework versions](tasks/07-installed-versions.md) | none | `aicsbench/aicsbench` | [#17](https://github.com/AICSbench/aicsbench/issues/17) |
| 8 | [Framework version per run, pinned stacks](tasks/08-stack-versions.md) | 7 | `aicsbench/aicsbench` | [#18](https://github.com/AICSbench/aicsbench/issues/18) |
| 9 | [Custom model endpoints](tasks/09-custom-endpoints.md) | 1 | `aicsbench/aicsbench` | [#19](https://github.com/AICSbench/aicsbench/issues/19) |
| 6 | [Data repository files and workflows](tasks/06-data-repo.md) | 2, 4, [setup](setup.md) §1 and §3 | `aicsbench/data` | [#16](https://github.com/AICSbench/aicsbench/issues/16) |
| 10 | [SvelteKit site and deploy](tasks/10-web-site.md) | 4, [setup](setup.md) §1 and §2 | `aicsbench/web` | [#20](https://github.com/AICSbench/aicsbench/issues/20) |
| 5 | [Compare page in the browser](tasks/05-compare-page.md) | 1, 10 | `aicsbench/web` | [#15](https://github.com/AICSbench/aicsbench/issues/15) |

Task numbers are stable IDs, not an order; the table is grouped by
repository. Tasks 2, 7 and 9 can run in parallel once task 1 is merged
(7 does not even need 1), and task 4 follows task 2. The task 7 export
keys are added by whichever of tasks 4 and 7 merges second. Do 7 and 8 before real submissions start so
public runs carry exact, pinned versions. Tasks 6 and 10 wait for the
maintainer's setup steps.

## How to work on a task

1. Work in the repository the task names. Branch from its latest `main`
   with the branch name the task file gives.
2. Read the task file completely. Its **Acceptance criteria** are the
   definition of done; its **Out of scope** list is binding.
3. Keep the existing architecture: pydantic v2 models with
   `extra="forbid"`, typer commands in `src/ai_benchmark/cli/app.py`, pure
   functions that are easy to unit test, no network access in unit tests.
4. Before opening the PR, the repository's checks must pass. In
   `aicsbench/aicsbench` that is (see CONTRIBUTING.md):

   ```bash
   pytest
   ruff check src tests benchmarks
   ruff format --check src tests benchmarks
   mypy src
   ```

   In `aicsbench/data` run what its CI runs; in `aicsbench/web` that is
   `pnpm install --frozen-lockfile && pnpm check && pnpm lint && pnpm test && pnpm build`.
5. Update the docs the task names. In the PR description, use
   `Closes AICSbench/aicsbench#N` only if merging the PR completes the task.
   For partial work, use `Refs AICSbench/aicsbench#N` and leave the issue open.

## Conventions

- **Commits:** Conventional Commits 1.0, `type(scope): imperative summary`
  of at most 72 characters, a body explaining why, one logical change per
  commit, and every commit passes the tests. New scopes for this phase:
  `submission`, `site`.
- **No attribution trailers.** No `Co-Authored-By`, `Generated-by` or
  similar lines in commits.
- **Pull requests:** title in the same format; body with a Summary, a
  Verification section listing what you actually ran, and the task file it
  implements. PRs are squash-merged by the maintainer, so the squash
  commit message is the PR title plus a short body.
- **Never** commit credentials, real result files from someone else's
  machine, or anything under `results/` or `.workspaces/`.
- Dependencies: Phase 5 adds no runtime dependency to `aicsbench`.
  SvelteKit, DuckDB-WASM and all other JavaScript live only in
  `aicsbench/web`.
