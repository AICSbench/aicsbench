# AI Coding Benchmark Platform

An open-source platform that measures and compares how productive AI coding
agents (Codex CLI, Claude Code, OpenCode, and more) are across programming
languages and frameworks.

> **Status: Phase 5 in progress (public results and submissions).** Trials run in Docker with the
> built-in Claude Code, Codex CLI and OpenCode adapters
> ([docs/agents.md](docs/agents.md)). Repair attempts, bounded parallel runs,
> comparison reports, a local dashboard, submission validation and submission
> pull requests (`aicsbench submit`), and installed language/framework version
> recording are available, and `aicsbench site export` writes the public site's
> data ([docs/phase-5/site-export.md](docs/phase-5/site-export.md)). The rest of
> the public results site is still planned in [Phase 5](docs/phase-5/README.md). See
> [ARCHITECTURE.md](ARCHITECTURE.md) for the full vision.

## Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
aicsbench --help
```

Requires Python 3.10+ and Docker (the `docker` CLI talking to a local daemon) to run trials.
To run on a server, follow [docs/vps-setup.md](docs/vps-setup.md).

Agents run inside each stack's container and authenticate with either a
subscription (default) or an API key. See
[docs/agent-execution.md](docs/agent-execution.md).

## Install from PyPI

```bash
pip install aicsbench
```

If you installed from git before the rename, uninstall the old distribution
first (`pip uninstall ai-benchmark`) — both distributions own the same
`ai_benchmark` package and `aicsbench` command, so uninstalling the old one
after the new install would delete the new files.

## CLI

```bash
aicsbench tasks                      # list benchmark tasks
aicsbench stacks                     # list language/framework stacks
aicsbench agents [--check --auth subscription]  # list adapters, check credentials
aicsbench validate [FILES...]        # validate task/stack/experiment files
aicsbench validate-submission PATH... # validate submission bundles
aicsbench submit --run RUN_ID [--dry-run | --out DIR] [--with-logs]  # submit a run
aicsbench run --agents codex,claude:<model> --task task-management-api \
              --stacks fastapi,laravel --trials 5 --auth subscription --dry-run
aicsbench run --agents codex:<model> --task task-management-api \
              --stacks express-latest@5.2.1,express-latest@5.3.0 --dry-run
aicsbench run --config examples/experiment.yaml --dry-run
aicsbench results [--harness-version 0.167.2 --stack Y --mode standard --not-rate-limited ...]
aicsbench compare --run RUN_ID --by stack [--agent codex --model gpt-6-luna]
aicsbench compare --run RUN_ID --by stack,model
aicsbench compare --run RUN_ID --by harness_version
aicsbench compare --by model --tasks url-shortener-api,task-management-api@2 --agent claude
aicsbench site export --data ../data --out export --base-url https://aicsbench.github.io/web/
aicsbench export --run RUN_ID --format csv > trials.csv
aicsbench export --run RUN_ID --format jsonl > trials.jsonl
aicsbench report --run RUN_ID --by stack > report.md
aicsbench dashboard --port 8765
```

In results, the agent CLI is called the **harness** (`claude`, `codex`,
`opencode`); `--by harness_version` compares the versions it reported, and
`--by` takes up to three comma-separated dimensions. `--tasks` (alias
`--matrix`) compares the same groups across several tasks: one column per task
(newest version, or `<id>@<version>`), each exactly the single-task comparison,
plus an overall success rate, coverage and mean rank per row.

Use `--repairs N` to let a failed acceptance suite trigger up to N more agent
attempts in the same workspace (default 0, maximum 5). Use `--parallel N` to
run up to N isolated trials together (default 1, maximum 8). Each repair gets
its own agent timeout and logs. Reports refuse to mix trials with different
repair budgets or parallelism because their timings are not comparable.
Subscription runs share usage limits, so parallelism can increase limit hits.

The dashboard reads the JSON result store and uses the same comparison query
as `aicsbench compare`. It binds only to `127.0.0.1`. On a server, forward its
port from your Mac with `ssh -L 8765:127.0.0.1:8765 ubuntu@SERVER`, then open
`http://127.0.0.1:8765` locally.

The `fastapi-latest`, `express-latest`, and `laravel13-boost` stacks pin the
newer runtime and framework versions used for the updated benchmark. The
`laravel13-boost` stack provides a Laravel 13 starter project and installs
Laravel Boost before the agent starts. Boost guidelines, skills, and MCP tools
are available to supported agents. Setup time is outside the agent timer, and
this stack starts from a scaffold; compare it separately from the empty-folder
`laravel` (Laravel 11) baseline. For example:

```bash
aicsbench run --agents codex:gpt-6-luna --task task-management-api \
              --stacks fastapi-latest,laravel13-boost,express-latest \
              --trials 20 --auth subscription
```

Stacks marked `@version` in `aicsbench stacks` (`express-latest`,
`fastapi-latest` and `laravel13-boost`) take the framework version per run:
`--stacks express-latest@5.2.1`, or the same `id@version` entry in an
experiment config's `stacks`. The version must be exact (`x.y.z` with an
optional pre-release tag such as `5.3.0-beta.1`); ranges such as `^5.2`,
`5.2.x` or `5.2` are refused, and so is `@version` on any other stack. The
agent's prompt (`$framework_version`) and `environment.framework_version`
use the selected version. A trial of a version other than the stack's default
is recorded as stack `<id>@<version>`, so `compare --by stack` shows each
version as its own row; `--by framework` and `--by language` refuse to pool
different versions of one framework or language. One run may hold several
versions of one stack (`express-latest@5.2.1,express-latest@5.3.0`).
`laravel13-boost` defaults to Laravel 13.43.0 with Boost 2.10.4 from the
13.11.0 starter, which needs `laravel/framework` 13.17.0 or later.

The [20-trial Luna results](docs/luna-latest-20-trials.md) record the run IDs,
test outcomes, timing, token use, cost estimate, and differences in each
stack's starting state. Laravel setup retries transient Composer failures
before the agent starts.

Platform settings come from `BENCHMARK_*` environment variables (nested with
`__`, e.g. `BENCHMARK_DOCKER__DEFAULT_MEMORY_LIMIT=8g`) or global CLI options
such as `--results-dir`.

## Submitting results

Finished runs can be published on the public results site by pull request to
[`aicsbench/data`](https://github.com/aicsbench/data). One command packs the
run into a bundle, validates it with the same checks the data repository's CI
runs, and opens the pull request:

```bash
aicsbench submit --run RUN_ID --dry-run            # show the bundle and its validation
aicsbench submit --run RUN_ID --notes "M3 Max, 64 GB"   # fork, push and open the PR
aicsbench submit --run RUN_ID --out DIR            # write DIR/submissions/... to open the PR by hand
```

Opening the PR needs the [GitHub CLI](https://cli.github.com/) installed and
logged in (`gh auth login`). The submitter name defaults to `git config
user.name` (`--name`) and the GitHub handle to the account `gh` is logged in as
(`--github`). Every trial must be finished, and each needs its harness version.

The bundle is a sanitized copy; `results/` is never changed. Host details are
cut to platform, Python and machine, the run's results and workspace folders and
your home folder are replaced with `<results>`, `<workspace>` and `~`, a custom
endpoint host other than `openrouter.ai`, `api.anthropic.com` or
`api.openai.com` is published as `custom`, and every
file is scanned for secrets: a match refuses the whole bundle and nothing is
written or sent. Logs are included only with `--with-logs` (each trial's
`.workspaces/<run>/<trial>/logs/` as `logs/<trial>.tar.gz`); the scan is a
safety net, so read your logs before submitting them. PR mode refuses a run
that is already in `aicsbench/data`; with `--out` or `--dry-run`, pass
`--against DIR` (a checkout of the data repository) for that check. Submitted
result data is published under CC BY 4.0. The bundle format is in
[docs/phase-5/schemas.md](docs/phase-5/schemas.md#submission-bundle).

## Layout

```
src/ai_benchmark/
  cli/            Typer entry point (`aicsbench`)
  config/         Pydantic schemas: tasks, stacks, experiments, platform settings; YAML/JSON loader
  models/         Enums, metric schemas (with explicit availability), result records
  agents/         AgentAdapter interface, registry, built-in claude/codex/commandcode/opencode adapters
  environment/    ExecutionEnvironment interface, Docker implementation, per-trial workspaces
  orchestrator/   Planning (randomized, seeded), trial execution, git metrics, JUnit parsing
  dashboard.py     Loopback-only comparison dashboard
  storage/        ResultStore interface + atomic JSON file store
benchmarks/
  tasks/<id>/task.yaml   Benchmark task definitions
  stacks/<id>.yaml       Stack (language + framework + services) definitions
examples/experiment.yaml Example experiment matrix
tests/                   Unit tests (pytest); tests/integration needs Docker
```

## Extending

- **New agent:** subclass `ai_benchmark.agents.AgentAdapter` and register it with
  `@register_agent` or an `ai_benchmark.agents` entry point in your package. No core changes.
- **New stack:** add `benchmarks/stacks/<id>.yaml` with a pinned image or Dockerfile, and
  `environment_notes` telling the agent where its database is.
- **New task:** add `benchmarks/tasks/<id>/task.yaml` and an HTTP acceptance suite in
  `acceptance/` (see [docs/trial-execution.md](docs/trial-execution.md#writing-an-acceptance-suite)).

## Development

```bash
pytest
ruff check src tests benchmarks
ruff format --check src tests benchmarks
mypy src
BENCHMARK_DOCKER_TESTS=1 pytest tests/integration   # one real trial in Docker
```

How a trial runs, step by step: [docs/trial-execution.md](docs/trial-execution.md).

Phase 5 (public results site and submissions) is planned task by task in
[docs/phase-5/](docs/phase-5/README.md).
