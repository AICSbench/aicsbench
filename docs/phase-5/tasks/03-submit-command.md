# Task 3: `aicsbench submit`

**Branch:** `feat/phase5-submit-command` · **Depends on:** task 2 ·
**Commit scope:** `submission`, `cli`

## Why

Submitting must be one command after a run, and must never send something
the data repository would reject or that leaks local details.

## Command

```
aicsbench submit --run RUN_ID
                 [--name NAME] [--github HANDLE] [--notes TEXT]
                 [--with-logs] [--out DIR] [--dry-run]
                 [--data-repo aicsbench/data] [--against DIR]
```

1. Load the run from the result store. Refuse if any trial is still
   `pending`/`running`.
2. Build the bundle in a temporary folder with the layout and sanitizing in
   [schemas.md](../schemas.md#submission-bundle). `suite_hash_source` is
   `run` when the manifest has the hash, else compute it from the local
   catalog and use `submit`. `--with-logs` packs each trial's
   `.workspaces/<run>/<trial>/logs/` into `logs/<trial_id>.tar.gz`; a missing
   log folder is a warning and that trial gets no archive.
3. Run `validate_bundle` on it. Any problem: print them and exit 1, nothing
   is written or sent. In PR mode (step 6) the clones happen first, and
   validation passes `against=<upstream>/submissions`, where `<upstream>` is
   a fresh shallow clone of the upstream `aicsbench/data` default branch
   (not the fork, which may be behind upstream), so a run that is already
   in the data repository is refused before anything is pushed. The data
   repository's CI check against its base branch remains the backstop. With
   `--out` or `--dry-run` there is no clone; pass `--against DIR` (a
   checkout of `aicsbench/data`) to run the duplicate check, otherwise the
   command prints that it was skipped.
4. `--dry-run`: print the file list with sizes and the validation result,
   then exit 0.
5. `--out DIR`: copy the bundle to `DIR/submissions/<task>/<run>/` and stop
   (for people who open the PR by hand).
6. Otherwise open the PR with the GitHub CLI (`gh`), which must be installed
   and logged in (check `gh auth status`; if not, print the `--out`
   instructions and exit 1):
   - `gh repo fork <data-repo> --clone=false` (idempotent),
   - clone the fork shallowly into a temp dir, create branch
     `submit/<run_id>`, copy the bundle, commit as
     `data: add <task_id> run <run_id>` with the submitter as author, push,
   - `gh pr create --repo <data-repo>` with the title
     `data: add <task_id> run <run_id>` and a body listing harnesses,
     models, stacks, trial count and pass count, plus the licence line from
     decisions D5.
   Print the PR URL.
7. `--name` defaults to `git config user.name`; `--github` defaults to
   `gh api user --jq .login` when `gh` is logged in.

The git/`gh` calls go through one small wrapper class so tests can replace
it with a fake; no test touches the network or the real `gh`.

## Acceptance criteria

- [ ] `--dry-run` on a fixture run prints the bundle and exits 0 without
      creating files outside the temp dir.
- [ ] `--out` writes a bundle that `aicsbench validate-submission` accepts.
- [ ] A run containing a planted secret in `raw`, or in a log file with
      `--with-logs`, is refused with the `secret` problem, and nothing is
      written.
- [ ] No absolute path from the host appears anywhere in the bundle
      (test greps the bundle for `tmp_path`).
- [ ] With a stale fork that lacks a run already merged upstream, PR mode
      refuses that run as a duplicate.
- [ ] With the fake `gh` wrapper, the command issues fork, clone, branch,
      commit, push and `pr create` in that order, with the documented
      branch name, commit message and title.
- [ ] README gets a "Submitting results" section.

## Out of scope

Any server-side upload. Changing the data repository.
