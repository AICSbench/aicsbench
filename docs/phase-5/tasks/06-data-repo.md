# Task 6: data repository files and workflows

**Repository:** `aicsbench/data` · **Branch:** `ci/bootstrap` ·
**Depends on:** tasks 2 and 4 merged in `aicsbench/aicsbench`, and the
manual setup in [setup.md](../setup.md) (the repository exists) ·
**Commit scope:** `ci`, `docs`

## Why

The data repository is where submission PRs arrive and are validated. On
every merge it publishes the site export and tells `aicsbench/web` to
rebuild. Its files are written straight into it (decision D9). No template
copy is kept in `aicsbench/aicsbench`.

## Files

```
README.md                         # what this repo is, how to submit, licence (D5)
LICENSE-DATA                      # CC BY 4.0 text reference
maintainers.txt                   # one GitHub handle per line: dyaskur
verified-runs.txt                 # one run ID per line, empty at first
CODEOWNERS                        # verified-runs.txt, maintainers.txt, CODEOWNERS, .github/ → @dyaskur
submissions/.gitkeep
.github/pull_request_template.md  # checklist: ran aicsbench submit, agrees to licence
.github/scripts/guard.py          # file-and-author rules, pure function + CLI
.github/workflows/validate.yml    # pull_request: runs the PR's content, no privileges
.github/workflows/guard.yml       # pull_request_target: base-branch code only, never the PR's
.github/workflows/publish.yml     # push to main: export, commit to `export`, dispatch to web
.github/workflows/test-guard.yml  # pull_request: unit tests for guard.py
tests/test_guard.py
```

The two PR workflows split trust on purpose. GitHub runs a `pull_request`
workflow from the PR's own merge commit, so a PR can rewrite `validate.yml`
and make it pass. `guard.yml` runs on `pull_request_target`, which always
takes its workflow file from the base branch, and it never checks out or
executes anything from the PR. Branch protection requires **both** checks.

The benchmark is always installed the same way, at a pinned ref held in one
`env:` line per workflow:
`pip install "ai-benchmark @ git+https://github.com/aicsbench/aicsbench@<ref>"`.
The distribution is named `ai-benchmark`, and it installs the `aicsbench`
command.

### `validate.yml`

Runs on `pull_request`, with `permissions: contents: read` and no secrets.

1. Check out the PR. Check out the base branch into `base/` as well (a
   second `actions/checkout` with `ref: ${{ github.base_ref }}` and
   `path: base`).
2. Install the benchmark.
3. List the submission folders the PR adds (diff against the base).
4. Run `aicsbench validate-submission <added folders> --against base/submissions/`
   (admission mode) with the benchmark's own task catalog.
5. Run `aicsbench site export --data . --out "$RUNNER_TEMP/export" --base-url https://example.invalid/`
   as a smoke test. A slug collision or any other export failure fails the
   PR here, before merge.

### `guard.yml`

Runs on `pull_request_target`, with `permissions: contents: read,
pull-requests: read`. It never checks out the PR head and never executes PR
files.

1. Check out the **base** branch only. That is the default for
   `pull_request_target`; never pass the PR's ref or SHA.
2. List the PR's changed files with the GitHub API
   (`gh api repos/{owner}/{repo}/pulls/{n}/files --paginate`), treating
   file names as data.
3. Run `python .github/scripts/guard.py` (the base branch's copy) with the
   file list, the PR author's login and `maintainers.txt` from the base
   checkout. It fails when:
   - any change under `submissions/` is a modification, rename or deletion
     (only additions are allowed);
   - the PR touches `verified-runs.txt`, `maintainers.txt`, `CODEOWNERS` or
     `.github/`, unless the author is listed in the base branch's
     `maintainers.txt`, so a PR cannot add its own author;
   - a non-maintainer PR touches anything outside `submissions/`.

### `publish.yml`

Runs on push to `main` and on `workflow_dispatch`.

1. Job `export`, with `permissions: contents: write`:
   - Install the benchmark.
   - Run `aicsbench site export --data . --out "$RUNNER_TEMP/export" --base-url "$BASE_URL"`.
     `BASE_URL` comes from a repository variable, with the default from
     decision D2.
   - Commit the export as the whole content of the `export` branch. Create
     the branch as an orphan the first time. The commit message is
     `data: export <short main sha>`, and the author is
     `github-actions[bot]`. Push without force.
   - Expose the `main` commit as an output.
2. Job `notify-web`, which needs `export`, with `permissions: {}`:
   - `gh api repos/aicsbench/web/dispatches -f event_type=data-published -f client_payload[data_commit]=<sha>`
     with `GH_TOKEN: ${{ secrets.WEB_DISPATCH_TOKEN }}`.
   - This is the only step that sees the token. No `pull_request` workflow
     can read it.
3. `concurrency: publish` with `cancel-in-progress: false`, so exports never
   interleave. A newer merge queues behind the running one.

All workflows: least-privilege `permissions:` per job, every third-party
action pinned to a full commit SHA with the version in a comment, and no
secrets available to anything that runs PR content.

## Acceptance criteria

- [ ] Every file above exists.
- [ ] All workflow files pass `actionlint` (run it if available; say in the
      PR if it was not run).
- [ ] `tests/test_guard.py` covers each rule: changed-file list,
      maintainer list and author in, pass or fail out. It also has a test
      that parses `guard.yml` and asserts there is no `actions/checkout`
      of the PR head (no `ref:` or `repository:` pointing at the PR) and no
      step that runs a file from the PR.
- [ ] `test-guard.yml` runs those tests on pull requests.
- [ ] Every third-party action is pinned to a full commit SHA, with the
      version in a comment.
- [ ] `WEB_DISPATCH_TOKEN` appears only in `publish.yml`'s `notify-web`
      job.
- [ ] The first PR's description shows a manual run of `validate.yml`'s
      steps against the fixture bundle from `aicsbench/aicsbench`
      (`tests/fixtures/submissions/`) copied into `submissions/`.

## Out of scope

Creating the repository and its settings (manual, [setup.md](../setup.md)).
Rendering HTML (task 10).
