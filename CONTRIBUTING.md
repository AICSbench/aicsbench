# Contributing

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

Before opening a pull request, all of these must pass:

```bash
pytest
ruff check src tests benchmarks scripts
ruff format --check src tests benchmarks scripts
mypy src scripts
```

GitHub Actions runs these checks on Python 3.10 and 3.13, validates the
installed CLI and benchmark definitions, and runs the Docker integration
tests in a separate job. To run the latter locally with a running Docker daemon:

```bash
BENCHMARK_DOCKER_TESTS=1 pytest tests/integration
```

The formatter excludes acceptance suites because their exact bytes identify
published task versions. Lint and the suite-hash regression test still check them;
see **Benchmark tasks** below before changing a suite.

## Commit messages

We follow [Conventional Commits 1.0](https://www.conventionalcommits.org/en/v1.0.0/).

```
<type>(<optional scope>): <summary>

<body: what changed and why>

<optional footer, e.g. BREAKING CHANGE: ... or Refs #12>
```

**Summary line**

- Imperative mood, lower case, no trailing period: `add`, not `added` / `adds`.
- At most 72 characters including the type and scope.

**Types**

| Type | Use for |
| --- | --- |
| `feat` | A new capability for users or extension authors |
| `fix` | A bug fix |
| `docs` | Documentation only |
| `test` | Adding or changing tests only |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `perf` | Performance improvement |
| `build` | Packaging, dependencies, Docker images |
| `ci` | CI configuration |
| `chore` | Maintenance that doesn't fit the above |

**Scopes** match the package layout: `models`, `config`, `agents`,
`environment`, `orchestrator`, `storage`, `cli`, plus `architecture` for
ARCHITECTURE.md. A new agent adapter uses its name, e.g. `feat(agents/codex): ...`.

**Body** explains why the change is needed and anything a reviewer can't
see from the diff. Wrap at 72 characters.

**Breaking changes** to a schema, the adapter interface or the CLI add `!`
after the type/scope (`feat(models)!: ...`) and a `BREAKING CHANGE:` footer
describing the migration.

**One logical change per commit.** Each commit should build and pass the
tests on its own.

Do not add co-author or other attribution trailers.

## Benchmark tasks

Each task version is immutable once a run has been recorded against it: the
submission validator pins the acceptance suite by its SHA-256 hash
(`docs/phase-5/schemas.md`). Changing a task's acceptance suite therefore
requires a new task version; editing the suite in place makes existing runs
unsubmittable. `tests/fixtures/suite-hashes.json` records the hash of every
shipped task version and a test fails when it drifts.

## Branches and pull requests

- Branch from `main` as `<type>/<short-description>`, e.g. `feat/codex-adapter`.
- Pull request titles follow the same Conventional Commits format.
- Never commit secrets. Configs reference credentials by environment
  variable name or file path only (see docs/agent-execution.md).

### Smoke test evidence

- After running a smoke test for a pull request, post the result as a comment
  on that PR, even if the test fails. Include the tested commit, the scenario
  and relevant versions, and the observed result (such as acceptance counts
  and a run ID when safe to share).
- Explain why the result supports merging or what still blocks it. Separate
  what the test verified from anything it did not cover. If the test could
  not run, report it as blocked, not passed.
- Report the environment generically (for example, Docker on Linux ARM64).
  Omit server IPs, hostnames, SSH details, credentials, private URLs and
  paths, and raw logs that may expose them.
