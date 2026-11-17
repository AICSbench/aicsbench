# Task 8: choose the framework version per run, and pin every stack

**Branch:** `feat/phase5-stack-versions` · **Depends on:** task 7 (so the
installed version is recorded and can be checked) · **Commit scope:**
`stacks`, `config`, `environment`, `cli`

## Why

Today a framework version is chosen only by writing a new stack file. The
maintainer wants to pick it when starting a run
(`--stacks express-latest@5.2.1`). Also, `laravel13-boost` is not pinned:
setup runs `composer create-project laravel/laravel ... ^13.0` and
`composer require laravel/boost` with no version, so two builds can test
different releases while reporting the same `framework_version: "13"`.

## Changes

1. **Pin the defaults.** In `laravel13-boost.yaml` (and `laravel.yaml` if
   it has the same pattern), install exact versions:
   `composer create-project laravel/laravel /tmp/laravel-starter <x.y.z>`,
   then `composer require laravel/framework:<x.y.z> laravel/boost:<a.b.c>`.
   Set `framework_version` to the full `x.y.z`. Pick the versions the VPS
   resolves today (record the probe output from task 7 in the PR).
2. **Stack version parameter.** `StackSpec` gets
   `framework_version_param: bool = False`. When true, the stack's setup
   commands and Dockerfile take the framework version from the
   `FRAMEWORK_VERSION` environment variable / build arg (the Dockerfile
   declares `ARG FRAMEWORK_VERSION`). The default is `framework_version`.
   Enable it on `express-latest`, `fastapi-latest` and `laravel13-boost`.
   Their `environment_notes` must not hard-code the version (today
   `express-latest` says "Express 5.3.0 is installed"); use
   `$framework_version` or drop the number.
   For FastAPI, `requirements.txt` no longer pins `fastapi`; the Dockerfile
   installs `fastapi==${FRAMEWORK_VERSION}` after the requirements.
3. **Selecting a version.** A stack entry in `--stacks` and in the
   experiment config's `stacks` may be `id@version`:
   - The version must match
     `^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?$`: exactly
     three numeric parts and an optional pre-release tag, so no ranges
     (`^`, `~`, `*`, `>=`, `5.2.x`, `5.2`), and nothing that could be shell
     syntax. Anything else is a usage error (exit 2).
   - `@version` on a stack without `framework_version_param` is a usage
     error that names the stack.
   - The same id may appear with different versions in one run
     (`express-latest@5.2.1,express-latest@5.3.0`); the uniqueness check
     applies to the full `id@version` string.
4. **Images.** The image cache tag includes the version
   (`ai-benchmark/stack-<id>-<version>:<hash>`), so versions never share a
   cached image. The build passes `--build-arg FRAMEWORK_VERSION=<v>`, and
   setup commands get `FRAMEWORK_VERSION` in their environment.
5. **Results.** For a trial with an explicit version that differs from the
   stack default:
   - `environment.framework_version` is the selected version.
   - `environment.stack` is `<id>@<version>`.

   With no `@`, or with `@` set to the default version, `stack` stays
   `<id>`, so existing results and comparisons are unchanged. Because
   `--by stack` groups by `environment.stack`, two versions show as two rows
   and are never averaged together. Stack-axis comparisons still require
   one model (existing rule).

   `--by framework` and `--by language` group by name only. `compare_trials`
   therefore refuses them when one group mixes declared versions, with
   `"framework comparison mixes versions of one framework; compare by stack
   or filter to one version"` (and the same for language). This also covers
   two stack files that declare different versions, which is possible
   today.
6. **Mismatch check.** After task 7's probes run, if
   `installed_versions["framework"]` exists and does not equal the selected
   version, log a warning and record it in the trial's `error` field. Do
   not fail the trial (the agent may have changed it). The site shows these
   trials with a "version changed during trial" note.
7. **Dry run and listing.** `aicsbench run --dry-run` shows the resolved
   `id@version` per trial. `aicsbench stacks` shows which stacks accept a
   version.
8. **Docs.** README run examples and `docs/trial-execution.md` describe
   `@version`. Point out that the agent's prompt
   (`$framework_version`) uses the selected version.

## Acceptance criteria

- [ ] `--stacks express-latest@5.2.1` plans trials whose stack label is
      `express-latest@5.2.1` and whose prompt and `framework_version` say
      `5.2.1`. With a fake Docker CLI, the build receives
      `--build-arg FRAMEWORK_VERSION=5.2.1` and the image tag contains
      `5.2.1`.
- [ ] `--stacks express-latest@^5.2`, `@5.2.x`, `@5.2`, `@latest`,
      `@5.2;rm -rf /`, and `@1.0.0` on a stack without the parameter each exit 2 with a clear
      message, and nothing runs.
- [ ] `--stacks express-latest@5.3.0` (the default) gives stack label
      `express-latest`.
- [ ] `aicsbench compare --by stack` on a run with two versions of one
      stack shows two rows, and `--by framework` on the same run is
      refused with the versions message.
- [ ] A stored trial from before this change loads and compares exactly as
      before.
- [ ] `laravel13-boost.yaml` contains no version range; a test asserts that
      no stack setup command uses `^`, `~` or `*` in a version.
- [ ] The installed-vs-selected mismatch is recorded and warned, not
      failed.

## Out of scope

Versioning the language runtime per run (needs a different base image; do
it with separate stack files). Choosing versions of services.
