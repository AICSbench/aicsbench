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
.github/workflows/validate.yml
.github/workflows/publish.yml
```

`validate.yml` (on `pull_request`):

1. Checkout with full history of `main` for the duplicate check.
2. Install the benchmark at a pinned tag or commit:
   `pip install "ai-benchmark[site] @ git+https://github.com/dyaskur/ai-coding-stack-bench@<ref>"`
   (the distribution is named `ai-benchmark`; it installs the `aicsbench` command)
   (the ref lives in one `env:` line).
3. List the submission folders the PR adds; fail if the PR modifies or
   deletes anything that already exists on `main` under `submissions/`.
4. Run `aicsbench validate-submission <added folders> --against submissions/`
   with the benchmark's own task catalog.
5. Fail if the PR touches `verified-runs.txt`, `maintainers.txt` or
   `.github/` and the PR author is not in `maintainers.txt`.
6. Build the site into a temp dir as a smoke test.

`publish.yml` (on push to `main`, plus manual dispatch):

1. Same install step.
2. `aicsbench site build --data . --out _site --base-url "$BASE_URL"`
   (`BASE_URL` from a repository variable, default from decisions D2).
3. Upload with `actions/upload-pages-artifact` and deploy with
   `actions/deploy-pages`, both pinned to a full commit SHA.

Both workflows: least-privilege `permissions:`, `concurrency` so only the
latest publish runs, no secrets needed for PRs from forks.

## Part B: bootstrap the real data repository (maintainer step)

The coding agent cannot create GitHub repositories. Document these steps in
`docs/phase-5/data-repo.md` for the maintainer:

1. Create the public repository `dyaskur/aicsbench-data` (D1), empty.
2. Copy `templates/data-repo/` into it and push.
3. Settings → Pages → Source: GitHub Actions.
4. Settings → Branches: protect `main`, require the `validate` check and one
   review.
5. Submit the existing verified run (`docs/luna-latest-20-trials.md`) with
   `aicsbench submit` from the benchmark server and add its run IDs to
   `verified-runs.txt` in the same or a follow-up PR.

## Acceptance criteria

- [ ] `templates/data-repo/` exists with every file above.
- [ ] Both workflow files pass `actionlint` (run locally if available; say
      in the PR if it was not run).
- [ ] Every third-party action is pinned to a full commit SHA with the
      version in a comment.
- [ ] A test in this repo builds the site from `templates/data-repo/` plus
      the task 2 fixture bundle copied into `submissions/`, proving the
      template and the commands fit together.
- [ ] `docs/phase-5/data-repo.md` lists Part B step by step.

## Out of scope

Creating the repository, custom domains, analytics.
