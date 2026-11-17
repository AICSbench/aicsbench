# Phase 5 decisions

Decisions marked **Default (pending)** were not made by the maintainer yet.
Build against the default; it is chosen so that changing it later touches
configuration, not code. Do not change a default in a task PR. If a task
cannot be done with a default, stop and say so in the PR description.

| # | Question | Decision | State |
| --- | --- | --- | --- |
| D1 | Where does submitted data live? | A separate public repository, `dyaskur/aicsbench-data`. | Default (pending) |
| D2 | Hosting and domain | GitHub Pages from the data repository, served at `https://dyaskur.github.io/aicsbench-data/`. The base URL is a setting (`--base-url`), so a custom domain is a config change. | Default (pending) |
| D3 | Site generator | Python + Jinja2 inside this package (`aicsbench site build`), so the site reuses `comparison.py` and the result models and shows the same numbers as the CLI. | Default (pending) |
| D4 | Do community runs need logs? | No. Logs are optional for everyone; runs with logs get a "logs available" badge. Default rankings show verified runs only. | Default (pending) |
| D5 | Data licence | Submitted result data is published under CC BY 4.0; submitting implies agreement (stated in the PR template). Code stays under the repository's licence. | Default (pending) |
| D6 | Submission path | Pull requests only. No upload API. | Decided by the maintainer |
| D7 | What a merge produces | Static HTML for search engines plus a data file the browser queries for filters and custom comparisons. | Decided by the maintainer |
| D8 | Harness | Every trial records its harness (the agent CLI, for example Claude Code, Codex, OpenCode) and the harness version; the site compares by harness, harness version and harness + model. | Decided by the maintainer |

## Derived defaults (follow these, they are not open questions)

- **"Harness" in code.** The harness is the existing `TrialResult.agent.name`
  (the adapter name: `claude`, `codex`, `opencode`) and its version is
  `TrialResult.agent.version`. Code keeps the field names; user-facing text
  (site, CLI help, export columns added in Phase 5) says "harness".
- **Browser data file.** `data/trials.json` (an array of export rows, see
  [schemas.md](schemas.md#site-data-file)). It is small at this scale and
  needs no extra Python dependency. DuckDB-WASM reads it directly. Moving to
  Parquet later only changes the build step and the file name.
- **Verified runs.** A run is verified when its run ID is listed in
  `verified-runs.txt` at the root of the data repository. Only maintainers
  change that file (enforced by `CODEOWNERS` and a CI check that fails a PR
  touching it unless the PR author is in `maintainers.txt`). The label never
  comes from the bundle's own contents.
- **JavaScript.** Plain ES modules, no build step, no framework. External
  scripts only from `cdn.jsdelivr.net/npm/` with an exact pinned version.
