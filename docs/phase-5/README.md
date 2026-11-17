# Phase 5: public results through pull requests

Phase 5 makes benchmark results public. Anyone can submit a run as a pull
request to a data repository, and every merge there rebuilds a static,
search-engine-friendly website plus a data file that the browser queries
for filtering and custom comparisons. No application server, no accounts.

This folder is written so that an AI coding agent (or a person) can pick up
one task and finish it without further context. Read this page first, then
[decisions.md](decisions.md), [schemas.md](schemas.md) and the one task file
you are working on.

## Goal

1. `aicsbench submit --run RUN_ID` packs a finished run into a bundle and
   opens a PR on the data repository.
2. CI on the data repository validates the bundle with the same code
   (`aicsbench validate-submission`).
3. A merge to the data repository's `main` runs `aicsbench site build`,
   which renders static HTML and writes the data file, and deploys the site.
4. Visitors compare results by **harness** (the agent CLI: Claude Code,
   Codex, OpenCode, ...), harness version, model, harness + model, stack,
   language, framework and task, with filters, across runs.

```
run ──► aicsbench submit ──► PR on data repo ──► CI: validate-submission
                                                     │ fail: fix, push again
                                                     ▼ pass
                         deploy site ◄── aicsbench site build ◄── review + merge
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

| # | Task | Depends on | Repo |
| --- | --- | --- | --- |
| 1 | [Harness fields and dimensions](tasks/01-harness-dimensions.md) | none | this repo |
| 2 | [Submission bundle and validator](tasks/02-bundle-and-validator.md) | 1 | this repo |
| 3 | [`aicsbench submit`](tasks/03-submit-command.md) | 2 | this repo |
| 4 | [Static site generator](tasks/04-site-generator.md) | 1 | this repo |
| 5 | [Compare page in the browser](tasks/05-compare-page.md) | 4 | this repo |
| 6 | [Data repository template and workflows](tasks/06-data-repo.md) | 2, 4 | this repo, then the data repo |
| 7 | [Installed language and framework versions](tasks/07-installed-versions.md) | none | this repo |

Tasks 2 and 4 can run in parallel once task 1 is merged. Task 7 is independent and can run at any time; do it before real submissions start so public runs carry installed versions.

## How to work on a task

1. Branch from the latest `main` as `<type>/phase5-<short-name>`, for
   example `feat/phase5-harness-dimensions`.
2. Read the task file completely. Its **Acceptance criteria** are the
   definition of done; its **Out of scope** list is binding.
3. Keep the existing architecture: pydantic v2 models with
   `extra="forbid"`, typer commands in `src/ai_benchmark/cli/app.py`, pure
   functions that are easy to unit test, no network access in unit tests.
4. Before opening the PR, all of these must pass (see CONTRIBUTING.md):

   ```bash
   pytest
   ruff check src tests benchmarks
   ruff format --check src tests benchmarks
   mypy src
   ```

5. Update the docs the task names, and set the task's row in **Status**
   below to "done in #N" in the same PR.

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
- Dependencies: new runtime dependencies go into an optional extra
  (`site`), not the core install, so `aicsbench run` stays light.

## Status

| Task | Status |
| --- | --- |
| 1 Harness fields and dimensions | not started |
| 2 Submission bundle and validator | not started |
| 3 `aicsbench submit` | not started |
| 4 Static site generator | not started |
| 5 Compare page | not started |
| 6 Data repository | not started |
| 7 Installed versions | not started |
