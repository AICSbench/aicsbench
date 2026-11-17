# Task 10: website renderer and deploy

**Repository:** `aicsbench/web` · **Branch:** `feat/static-site` ·
**Depends on:** task 4 merged in `aicsbench/aicsbench`, and the manual setup
in [setup.md](../setup.md) (the repository exists, Pages is on) ·
**Commit scope:** `site`, `ci`

## Why

Search engines and people without JavaScript must see the results as
ordinary pages. The pages render the export from `aicsbench site export`, so
they show the CLI's numbers without recomputing anything (decisions D3, D9).

## Layout of the repository

```
pyproject.toml          # name "aicsbench-web", deps: jinja2>=3.1; dev: pytest, ruff, mypy
src/aicsbench_web/
  render.py             # export dir in, site dir out
  charts.py             # inline SVG bar charts from section rows
  templates/            # base.html (SEO head) + one template per page kind
  static/site.css
tests/fixtures/export/  # copied from aicsbench/aicsbench tests/fixtures/site-export/
.github/workflows/ci.yml
.github/workflows/deploy.yml
README.md               # what this repo is, how to preview locally
```

## Changes

1. `python -m aicsbench_web.render --export DIR --out DIR` renders:
   - every page in `pages.json`, through the template for its `kind`;
   - `sitemap.xml` and `robots.txt`;
   - `data/` and `runs/` copied unchanged from the export.

   It reads `export.json` first and fails clearly if `export_schema` is not
   one it supports (start with `{1}`).
2. Templates:
   - Leaderboards show the success rate, mean agent time ± standard
     deviation, mean cost and trial count, with a low-confidence marker.
   - A `null` value shows "unavailable", never `0` or blank.
   - `refused` sections show their message.
   - Community sections are labelled as such.
   - No external fonts or scripts on static pages.
3. `charts.py`: inline SVG bars for pass rate and for mean agent time with a
   standard deviation whisker. Colours come from CSS custom properties, so
   light and dark themes both work.
4. Escaping:
   - The Jinja2 `Environment` uses
     `autoescape=select_autoescape(["html", "xml"])`.
   - No template uses `|safe` on export data.
   - JSON-LD is written with `json.dumps`, with every `<`, `>` and `&`
     replaced by `<`, `>` and `&`.
5. SEO: every page has a unique `<title>`, a meta description, a canonical
   link built from `export.json.base_url`, Open Graph tags and JSON-LD
   (`Dataset` on task pages, `WebSite` on `/`).
6. `deploy.yml`, triggered on `repository_dispatch` (type
   `data-published`), on push to `main`, and on `workflow_dispatch`:
   - Check out this repository, and check out `aicsbench/data` at branch
     `export` (public, so no token) into `export/`. When the dispatch
     payload carries `data_commit`, check that `export/export.json` has the
     same `data_commit` and fail if it does not. This catches a stale branch.
   - Install this package, render to `_site`, then upload with
     `actions/upload-pages-artifact` and deploy with `actions/deploy-pages`.
   - Every third-party action is pinned to a full commit SHA, with the
     version in a comment.
   - Permissions: `contents: read`, plus `pages: write` and
     `id-token: write` on the deploy job only. `concurrency: pages` with
     `cancel-in-progress: true`.
7. `ci.yml` on pull requests: ruff, mypy, pytest. It also runs a contract
   job: install `aicsbench` at the ref in one `env:` line, run
   `aicsbench site export` on that repo's fixture submissions, then render
   the result. If either repository breaks the contract, this job fails.
8. README: local preview with
   `python -m aicsbench_web.render --export tests/fixtures/export --out _site && python -m http.server -d _site`.

## Acceptance criteria

- [ ] Rendering the fixture export produces every page kind, and the test
      finds a known pass rate, harness name and run ID in the HTML.
- [ ] Every page has a unique `<title>`, a meta description, a canonical
      link starting with `base_url`, and valid JSON-LD (parsed in the test).
- [ ] `sitemap.xml` lists every page, and `robots.txt` references it.
- [ ] The fixture's `<script>alert(1)</script>` in submitter and notes
      renders escaped on every page, and no JSON-LD block contains a raw
      `</script>`.
- [ ] Pages render with JavaScript disabled (no content depends on JS).
- [ ] An export with `export_schema: 2` fails the render with a message
      naming the supported version.
- [ ] The workflows pass `actionlint` (say in the PR if it was not run).
      Every third-party action is SHA-pinned, and no workflow has write
      permissions beyond the deploy job's.

## Out of scope

Computing anything from trial data (that is task 4). The compare page
(task 5). Custom domains and analytics.
