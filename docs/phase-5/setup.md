# Phase 5 manual setup (maintainer only)

A coding agent cannot create organizations, transfer repositories, install
apps or create tokens. The maintainer does these steps by hand, in this
order. Each step names the task that needs it.

## 1. Organization and repositories (before tasks 6 and 10)

1. Create the GitHub organization `aicsbench`.
2. Transfer `dyaskur/ai-coding-stack-bench` to it and rename it to
   `aicsbench`: Settings → General → Transfer, then Rename. GitHub redirects
   the old URLs. Update local clones with
   `git remote set-url origin https://github.com/aicsbench/aicsbench.git`,
   on the VPS too.
3. Create two empty public repositories, `aicsbench/data` and
   `aicsbench/web`. Add a README so `main` exists.
4. Install the CodeRabbit app on the organization, covering all
   repositories, so all three get reviews.
5. Give coding agents access to the new repositories. For Claude, install
   the Claude GitHub App on the organization.

## 2. `aicsbench/web` (before task 10 deploys)

1. Settings → Pages → Source: **GitHub Actions**.
2. Optional: a custom domain later only changes the data repository's
   `BASE_URL` variable, `BASE_PATH` in `aicsbench/web`'s `deploy.yml`
   (`/web` today, empty on a custom domain) and the Pages setting
   (decision D2).

## 3. `aicsbench/data` (before task 6 is merged)

1. Create the dispatch token. Use a fine-grained personal access token
   owned by the organization (or a GitHub App), with access to the
   repository `aicsbench/web` only and the permission **Contents: Read and
   write**, which `repository_dispatch` requires. Give it an expiry and a
   calendar reminder to renew it.
2. Add it to `aicsbench/data` under Settings → Secrets and variables →
   Actions as the secret `WEB_DISPATCH_TOKEN`.
3. Add the repository variable `BASE_URL` = `https://aicsbench.github.io/web/`.
4. Protect `main` (Settings → Branches or Rulesets):
   - require the `validate` and `guard` status checks;
   - require one approving review, with **Require review from Code Owners**
     on (without it, `CODEOWNERS` only requests a review and does not
     enforce one);
   - disallow force pushes and deletions.
5. Protect `export`: only GitHub Actions may push (a ruleset that restricts
   updates, with the Actions integration as the only bypass), and no force
   pushes.

## 4. First data (after tasks 3, 4 and 6)

1. On the benchmark server, submit the existing verified run
   (`docs/luna-latest-20-trials.md`) with `aicsbench submit`.
2. Add its run ID to `verified-runs.txt` in the same PR or a follow-up one.
3. After the merge, check that `publish.yml` ran, the `export` branch
   updated, and `aicsbench/web`'s deploy ran and the site shows the run.
