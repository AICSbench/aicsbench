# Task 7: record installed language and framework versions

**Branch:** `feat/phase5-installed-versions` · **Depends on:** nothing ·
**Commit scope:** `models`, `orchestrator`, `stacks`

## Why

Public reports must say exactly which versions were tested. The harness
version is already detected (`agent.version` comes from the CLI's own
`--version`), and the model is recorded as requested and as reported by the
CLI. The language and framework versions are not: `EnvironmentRecord`
copies `language_version` and `framework_version` from the stack YAML. That
value is declared, not observed. For `laravel13-boost` it is only `13`, and
an agent can upgrade a package during the trial.

## Changes

1. `StackSpec` (`config/stack.py`): add
   `version_probes: dict[str, list[str]] = {}`, a name → command map run in
   the app container. The names `language` and `framework` have fixed
   meaning; any other name records an extra component (for example
   `laravel/boost`). Each command prints only the version on stdout.
2. `EnvironmentRecord` (`models/results.py`): add
   `installed_versions: dict[str, str] = {}`. It is optional with a default,
   so stored results from before this change still load.
3. Orchestrator: run every probe **after the agent finishes and before the
   acceptance tests**, so the record shows what was actually tested. Use a
   30 s timeout per probe. Strip the output and keep its first line. A probe
   that fails, times out or prints nothing is logged as a warning and left
   out. It never changes the trial status.
4. `reporting.trial_row`: add `language_version_installed` and
   `framework_version_installed` (from `installed_versions["language"]` /
   `["framework"]`, `None` when missing) at the end, after the task 1
   columns. Existing keys keep their meaning: `language_version` and
   `framework_version` stay the declared values.
5. Comparisons are unchanged: installed versions are recorded and shown,
   but they are not part of `comparability_key` or of any grouping, so two
   trials of one stack stay comparable even if a patch release differs.
   (Declared versions are not in `comparability_key` either; task 8 covers
   comparisons across declared versions.) The site shows the installed version next to
   the stack and lists every distinct installed version on the stack page.
6. Add probes to the stacks in `benchmarks/stacks/`:

   | Stack | `language` | `framework` | extra |
   | --- | --- | --- | --- |
   | `express*` | `node -p process.versions.node` | `node -p "require('express/package.json').version"` | |
   | `fastapi*` | `python -c "import platform; print(platform.python_version())"` | `python -c "import fastapi; print(fastapi.__version__)"` | |
   | `laravel*` | `php -r "echo PHP_VERSION;"` | `php -r "require 'vendor/autoload.php'; echo Illuminate\Foundation\Application::VERSION;"` | `laravel/boost` (boost stack only): `composer show laravel/boost \| sed -n 's/^versions : \* //p'` |

   Run them with `sh -lc` and the stack `workdir`, like `setup_commands`.
7. Docs: `docs/trial-execution.md` describes `version_probes` and when they
   run. `docs/phase-5/schemas.md` already lists the two new export keys.

## Acceptance criteria

- [ ] With a fake container runner, a stack with probes produces a trial
      whose `environment.installed_versions` holds the probe outputs, and
      the probes run after the agent step and before the acceptance step.
- [ ] A failing or empty probe leaves its key out, logs a warning, and the
      trial status is unchanged.
- [ ] A stored trial JSON without `installed_versions` still loads and
      exports, with the two new columns empty.
- [ ] `comparability_key` is unchanged (test with two trials that differ
      only in `installed_versions`).
- [ ] Every stack in `benchmarks/stacks/` declares `language` and
      `framework` probes (a test loads the catalog and asserts this).

## Out of scope

Detecting versions of services (databases). Making installed versions part
of comparability.
