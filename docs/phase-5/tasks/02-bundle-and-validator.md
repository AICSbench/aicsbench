# Task 2: submission bundle and validator

**Branch:** `feat/phase5-submission-validator` · **Depends on:** task 1 ·
**Commit scope:** `submission`, `models`, `orchestrator`, `cli`

## Why

The data repository's CI and `aicsbench submit` must agree on what a valid
bundle is. One implementation, used by both, prevents drift.

## Changes

1. New package `src/ai_benchmark/submission/`:
   - `models.py`: `SubmissionInfo`, `Submitter`, `Problem` exactly as in
     [schemas.md](../schemas.md#submissionjson).
   - `suite.py`: `suite_sha256(task_path_or_spec) -> str`, the algorithm in
     [schemas.md](../schemas.md#acceptance-suite-hash).
   - `secrets.py`: `SECRET_PATTERNS`, `SECRET_KEYS`,
     `find_secrets(value) -> list[str]` that walks any JSON-like value (keys
     and values) and returns the JSON paths of matches, and
     `scan_text(text) -> list[str]` for log files, all as in
     [schemas.md](../schemas.md#sanitizing).
   - `validate.py`: `validate_bundle(path, catalog, *, against=None,
     check_catalog=True) -> list[Problem]`, implementing every rule and both
     modes in [schemas.md](../schemas.md#validation-rules), including the
     safe reading of log archives. Return all problems, do not stop at the
     first.
2. `RunManifest`: add `acceptance_suite_sha256: str | None = None`;
   the orchestrator fills it in `build_manifest` from the task.
3. CLI: `aicsbench validate-submission PATH... [--against DIR] [--no-catalog-check]`. Prints
   `ok <path>` or one line per problem (`<code> <path>: <message>`), exits 1
   when any bundle has problems. Uses the normal catalog
   (`--tasks-dir`/`--stacks-dir`).
4. Test fixtures in `tests/fixtures/submissions/`: one valid bundle (two
   trials, built from the existing fake orchestrator output, not real data)
   and a helper that copies it to `tmp_path` and breaks one thing per test.
5. Docs: CONTRIBUTING.md states that changing a task's acceptance suite
   requires a new task version. `docs/phase-5/schemas.md` stays the source of truth; add a short
   "Validating a bundle" section to `docs/trial-execution.md` pointing to it.

## Acceptance criteria

- [ ] The valid fixture returns no problems.
- [ ] One test per problem code, each breaking exactly one rule and
      asserting exactly that code.
- [ ] A manifest written before this change (no `acceptance_suite_sha256`)
      still loads.
- [ ] `suite_sha256` is stable across runs and operating systems (test with
      a fixed tiny suite and a hard-coded expected hash), and ignores
      `__pycache__`.
- [ ] `find_secrets` flags each pattern in `SECRET_PATTERNS`, and a
      `SECRET_KEYS` key with a non-empty value (for example
      `{"access_token": "abc"}`), returning the JSON path; a test asserts no
      false positive on the valid fixture.
- [ ] A log archive containing a planted token fails with `secret`; an
      archive member named `../x` fails with `layout`. Separate cases for a
      log line `AWS_SECRET_ACCESS_KEY=...`, an `AKIA...` key ID and a JSONL
      line `{"access_token": "..."}` each fail with `secret`.
- [ ] `tests/fixtures/suite-hashes.json` lists the hash of every shipped
      task version's suite, and a test fails when a suite changes without a
      new task version.
- [ ] `--against DIR` containing the same bundle path does not report it as
      a duplicate of itself; a copy of it under another folder does.
- [ ] With `--no-catalog-check`, a bundle for an unknown task version passes
      `task` and `suite`, while a `run` bundle whose hash differs from its
      manifest still fails `suite`.
- [ ] `aicsbench validate-submission` exit codes: 0 valid, 1 problems,
      2 usage error.
- [ ] No network access anywhere in this task.

## Out of scope

Writing bundles (task 3), the data repository (task 6).
