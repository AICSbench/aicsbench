# Task 6: data repository template and workflows

**Branch:** `feat/phase5-data-repo-template` · **Depends on:** tasks 2
and 4 · **Commit scope:** `ci`, `docs`

## Why

The data repository is where PRs arrive, are validated and, once merged,
publish the site. Its files are kept as a template in this repository so
they are reviewed and versioned with the code they call.

## Part A: template in this repository

Create `templates/data-repo/` containing exactly what the data repository's
root should hold:

```
README.md                         # what this repo is, how to submit, licence (D5)
LICENSE-DATA                      # CC BY 4.0 text reference
maintainers.txt                   # one GitHub handle per line: dyaskur
verified-runs.txt                 # one run ID per line, empty at first
CODEOWNERS                        # verified-runs.txt, maintainers.txt, .github/ → @dyaskur
submissions/.gitkeep
.github/pull_request_template.md  # checklist: ran aicsbench submit, agrees to licence
.github/workflows/validate.yml   # pull_request: runs the PR's content, no privileges
.github/workflows/guard.yml      # pull_request_target: base-branch code only, never the PR's
.github/workflows/publish.yml
```

The two PR workflows split trust on purpose. GitHub runs a `pull_request`
workflow from the PR's own merge commit, so a PR can rewrite `validate.yml`
and make it pass. `guard.yml` runs on `pull_request_target`, whose workflow
file always comes from the base branch, and it never checks out or executes
anything from the PR. Branch protection requires **both** checks.

`validate.yml` (on `pull_request`, `permissions: contents: read`, no
secrets):

1. Check out the PR, and check out the base branch into `base/` (for
   example a second `actions/checkout` with `ref: ${{ github.base_ref }}` and
   `path: base`).
2. Install the benchmark at a pinned tag or commit:
   `pip install "ai-benchmark[site] @ git+https://github.com/dyaskur/ai-coding-stack-bench@<ref>"`
   (the distribution is named `ai-benchmark`; it installs the `aicsbench` command)
   (the ref lives in one `env:` line).
3. List the submission folders the PR adds (diff against the base).
4. Run `aicsbench validate-submission <added folders> --against base/submissions/`
   (admission mode) with the benchmark's own task catalog. The baseline is
   the base branch, so a new bundle is never compared with itself.
5. Build the site into a temp dir as a smoke test.

`guard.yml` (on `pull_request_target`, `permissions: contents: read,
pull-requests: read`, no checkout of the PR head, no execution of PR files):

1. Check out the **base** branch only (the default for
   `pull_request_target`; never pass the PR's ref or SHA).
2. List the PR's changed files with the GitHub API
   (`gh api repos/{owner}/{repo}/pulls/{n}/files --paginate`), treating
   file names as data.
3. Fail if any change is a modification, rename or deletion under
   `submissions/` (only additions are allowed).
4. Fail if the PR touches `verified-runs.txt`, `maintainers.txt`,
   `CODEOWNERS` or `.github/`, unless the PR author's login is listed in
   `maintainers.txt` **as read from the base branch checkout**, so a PR
   cannot add its own author.
5. Fail if a non-maintainer PR touches anything outside `submissions/`.

`publish.yml` (on push to `main`, plus manual dispatch):

1. Same install step.
2. `aicsbench site build --data . --out _site --base-url "$BASE_URL"`
   (`BASE_URL` from a repository variable, default from decisions D2).
3. Upload with `actions/upload-pages-artifact` and deploy with
   `actions/deploy-pages`, both pinned to a full commit SHA.

All workflows: least-privilege `permissions:` per job, every third-party
action pinned to a full commit SHA, `concurrency` so only the latest publish
runs, and no secrets available to anything that runs PR content.

## Part B: bootstrap the real data repository (maintainer step)

The coding agent cannot create GitHub repositories. Document these steps in
`docs/phase-5/data-repo.md` for the maintainer:

1. Create the public repository `dyaskur/aicsbench-data` (D1), empty.
2. Copy `templates/data-repo/` into it and push.
3. Settings → Pages → Source: GitHub Actions.
4. Settings → Branches: protect `main`; require the `validate` and `guard`
   status checks, one approving review, and **"Require review from Code
   Owners"** (without it, `CODEOWNERS` only requests a review and does not
   enforce one). Disallow force pushes.
5. Submit the existing verified run (`docs/luna-latest-20-trials.md`) with
   `aicsbench submit` from the benchmark server and add its run IDs to
   `verified-runs.txt` in the same or a follow-up PR.

## Acceptance criteria

- [ ] `templates/data-repo/` exists with every file above.
- [ ] All three workflow files pass `actionlint` (run locally if available;
      say in the PR if it was not run).
- [ ] `guard.yml` contains no `actions/checkout` of the PR head (`ref:` or
      `repository:` pointing at the PR) and runs no file from the PR; a test
      in this repo parses the YAML and asserts this.
- [ ] The guard's file-and-author rules live in one small script under
      `templates/data-repo/.github/scripts/guard.py` with unit tests here
      (changed-file list and maintainer list in, pass or fail out).
- [ ] Every third-party action is pinned to a full commit SHA with the
      version in a comment.
- [ ] A test in this repo builds the site from `templates/data-repo/` plus
      the task 2 fixture bundle copied into `submissions/`, proving the
      template and the commands fit together.
- [ ] `docs/phase-5/data-repo.md` lists Part B step by step.

## Out of scope

Creating the repository, custom domains, analytics.
