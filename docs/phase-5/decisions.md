# Phase 5 decisions

Decisions marked **Default (pending)** were not made by the maintainer yet.
Build against the default; it is chosen so that changing it later touches
configuration, not code. Do not change a default in a task PR. If a task
cannot be done with a default, stop and say so in the PR description.

| # | Question | Decision | State |
| --- | --- | --- | --- |
| D1 | Where does submitted data live? | A separate public repository, `aicsbench/data`. | Decided by the maintainer |
| D2 | Hosting and domain | GitHub Pages from `aicsbench/web`, served at `https://aicsbench.github.io/web/`. The base URL is a setting (`--base-url`), so a custom domain is a config change. | Default (pending) |
| D3 | Site generator | SvelteKit with `@sveltejs/adapter-static` in `aicsbench/web`: every content page is prerendered to static HTML for search engines, and the compare page queries the data file in the browser with DuckDB-WASM. The web app only renders the export that `aicsbench site export` writes; every number, grouping and page path is computed in this package with `comparison.py`, so the site shows the same numbers as the CLI. | Decided by the maintainer |
| D4 | Do community runs need logs? | No. Logs are optional for everyone; runs with logs get a "logs available" badge. Default rankings show verified runs only. | Default (pending) |
| D5 | Data licence | Submitted result data is published under CC BY 4.0; submitting implies agreement (stated in the PR template). Code stays under the repository's licence. | Default (pending) |
| D6 | Submission path | Pull requests only. No upload API. | Decided by the maintainer |
| D7 | What a merge produces | Static HTML for search engines plus a data file the browser queries for filters and custom comparisons. | Decided by the maintainer |
| D8 | Harness | Every trial records its harness (the agent CLI, for example Claude Code, Codex, OpenCode) and the harness version; the site compares by harness, harness version and harness + model. A local trial whose CLI reported no version still completes and shows as `unknown` in local comparisons (task 1), but such a run cannot be submitted: the `harness` admission rule rejects it, so every public trial has a version. | Decided by the maintainer |
| D9 | Repositories | GitHub organization `aicsbench` with three public repositories: `aicsbench/aicsbench` (this CLI and platform, transferred from `dyaskur/ai-coding-stack-bench`), `aicsbench/data` (submissions, validation, the published export) and `aicsbench/web` (SvelteKit site, compare page, Pages deploy). | Decided by the maintainer |

## Derived defaults (follow these, they are not open questions)

- **"Harness" in code.** The harness is the existing `TrialResult.agent.name`
  (the adapter name: `claude`, `codex`, `opencode`) and its version is
  `TrialResult.agent.version`. Code keeps the field names; user-facing text
  (site, CLI help, export columns added in Phase 5) says "harness".
- **One computation, rendered elsewhere.** `aicsbench site export` (in this
  repository) reads `aicsbench/data` and writes the site export: trial rows,
  every page with its precomputed leaderboard rows, and page paths. See
  [schemas.md](schemas.md#site-export). `aicsbench/web` renders that export
  and never recomputes a number. The export ships JSON Schemas for its
  files, and the web app's types and contract test come from them. The only
  logic the web app repeats is the compare page's SQL (task 5), which a
  contract test checks against `compare_trials`.
- **Cross-repository flow.** A merge to `aicsbench/data` runs the export,
  commits it to the data repository's `export` branch, and sends a
  `repository_dispatch` (`data-published`) to `aicsbench/web`, which
  rebuilds and deploys. The web build reads the public `export` branch and
  needs no token. Only the dispatch needs one (`WEB_DISPATCH_TOKEN`, see
  [setup.md](setup.md)).
- **Browser data file.** `data/trials.json` (an array of export rows, see
  [schemas.md](schemas.md#site-export)). It is small at this scale and
  needs no extra Python dependency. DuckDB-WASM reads it directly. Moving to
  Parquet later only changes the export step and the file name.
- **Verified runs.** A run is verified when its run ID is listed in
  `verified-runs.txt` at the root of the data repository. Only maintainers
  change that file. Two independent controls enforce it: branch protection
  with "Require review from Code Owners" (owners set in `CODEOWNERS`), and
  the `guard` check, which runs from the base branch on
  `pull_request_target` and reads `maintainers.txt` from the base branch, so
  a PR cannot add its own author. The label never comes from the bundle's
  own contents. See [tasks/06-data-repo.md](tasks/06-data-repo.md).
- **JavaScript.** Only in `aicsbench/web`: SvelteKit + TypeScript, Node
  LTS pinned in `.nvmrc`, pnpm with a committed lockfile, every dependency
  at an exact version. DuckDB-WASM comes from npm and its `.wasm` files are
  served from the site itself, so the site loads nothing from a third-party
  origin.
