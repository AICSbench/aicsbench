# Task 10: SvelteKit site and deploy

**Repository:** `aicsbench/web` · **Branch:** `feat/static-site` ·
**Depends on:** task 4 merged in `aicsbench/aicsbench`, and the manual setup
in [setup.md](../setup.md) (the repository exists, Pages is on) ·
**Commit scope:** `site`, `ci`

## Why

Search engines and people without JavaScript must see the results as
ordinary pages. The pages render the export from `aicsbench site export`, so
they show the CLI's numbers without recomputing anything (decisions D3, D9).
SvelteKit with `adapter-static` prerenders every content page to HTML at
build time. The same app hosts the interactive compare page (task 5).

## Layout of the repository

```
.nvmrc                         # Node LTS, exact version
package.json                   # exact versions; packageManager: pnpm@<exact>
pnpm-lock.yaml
svelte.config.js               # adapter-static, paths.base from BASE_PATH
src/lib/types/export.ts        # generated from the export's JSON Schemas, committed
src/lib/load-export.ts         # reads and validates the export at build time
src/lib/components/            # Leaderboard, BarChart (SVG), Metric, SeoHead, ...
src/routes/                    # one route per page kind in pages.json
static/
scripts/gen-types.ts           # JSON Schema -> TypeScript (json-schema-to-typescript)
tests/fixtures/export/         # copied from aicsbench/aicsbench tests/fixtures/site-export/
tests/                         # vitest unit tests, Playwright e2e
.github/workflows/ci.yml
.github/workflows/deploy.yml
README.md                      # what this repo is, how to preview locally
```

## Changes

1. **Reading the export.** `EXPORT_DIR` (default `tests/fixtures/export`)
   points at an export. At build time `load-export.ts` reads `export.json`
   and the files it lists, and validates each one against the export's own
   `schema/*.schema.json` with Ajv. It fails the build with the file and
   JSON path of the first error. It also refuses an `export_schema` it does
   not support (start with `[1]`), naming both numbers.
2. **Types.** `pnpm gen:types` turns the schemas in
   `tests/fixtures/export/schema/` into `src/lib/types/export.ts`. CI checks
   that the committed file is up to date.
3. **Routes and prerendering.**
   - Every page in `pages.json` is prerendered.
   - Routes use `export const prerender = true` and list their pages with
     `entries()` from `pages.json`. The paths are used exactly as the export
     gives them; the web app never builds a slug.
   - `trailingSlash = 'always'`.
   - `+layout.ts` sets `prerender = true` for the whole app. The build fails
     on any unlisted or broken link (`handleHttpError: 'fail'`).
4. **Components.**
   - `Leaderboard` shows the success rate, mean agent time ± standard
     deviation, mean cost, trial count and a low-confidence marker.
   - A `null` value shows "unavailable", never `0` or blank.
   - A `refused` section shows its message instead of a table.
   - Community sections are labelled as such.
   - `BarChart` draws inline SVG bars for pass rate and for mean agent time
     with a standard deviation whisker. Colours come from CSS custom
     properties, so light and dark themes both work.
5. **Escaping.**
   - All export strings render through normal Svelte expressions, which
     escape.
   - `{@html}` is used in exactly one place: `SeoHead`'s JSON-LD. It
     serializes with `JSON.stringify` and replaces every `<`, `>` and `&`
     with `\u003c`, `\u003e` and `\u0026` (Unicode escapes, not HTML entities), so a
     `</script>` in any value cannot close the script element.
   - An ESLint rule (`svelte/no-at-html-tags`) is on, with a single
     reviewed disable comment at that spot.
6. **SEO.** Every page has a unique `<title>`, a meta description, a
   canonical link built from `export.json.base_url`, Open Graph tags and
   JSON-LD (`Dataset` on task pages, `WebSite` on `/`). A prerendered
   `sitemap.xml` and `robots.txt` list every page. `data/` and `runs/` are
   copied from the export into the build output for download.
7. **Without JavaScript.** Content pages work fully as static HTML.
   Hydration may add interactivity (sorting, theme), but no content may
   depend on it.
8. **`ci.yml`**, on pull requests:
   - `pnpm install --frozen-lockfile`, `pnpm check` (svelte-check),
     `pnpm lint` (ESLint + Prettier), `pnpm test` (Vitest),
     `pnpm build`, and `pnpm test:e2e` (Playwright against
     `pnpm preview`).
   - A `contract` job: set up Python, install `aicsbench` at the ref in one
     `env:` line, run `aicsbench site export` on that repository's fixture
     submissions, then `EXPORT_DIR=<that export> pnpm build`. Building it
     validates it against the schemas. If either repository breaks the
     contract, this job fails.
9. **`deploy.yml`**, triggered on `repository_dispatch` (type
   `data-published`), on push to `main`, and on `workflow_dispatch`:
   - Check out this repository, and check out `aicsbench/data` at branch
     `export` (public, so no token) into `export/`.
   - When the dispatch payload carries `data_commit`, check that
     `export/export.json` has the same `data_commit`, and fail if it does
     not.
   - `pnpm install --frozen-lockfile`, then
     `EXPORT_DIR=export BASE_PATH=/web pnpm build`. Upload `build/` with
     `actions/upload-pages-artifact` and deploy with `actions/deploy-pages`.
   - Every third-party action is pinned to a full commit SHA, with the
     version in a comment.
   - Permissions: `contents: read`, plus `pages: write` and
     `id-token: write` on the deploy job only. `concurrency: pages` with
     `cancel-in-progress: true`.
10. **README:** local preview with `pnpm install && pnpm dev`, which uses
    the fixture export, or `EXPORT_DIR=/path/to/export pnpm build && pnpm preview`.

## Acceptance criteria

- [ ] Building with the fixture export prerenders every page kind, and a
      test finds a known pass rate, harness name and run ID in the HTML
      files.
- [ ] Every page has a unique `<title>`, a meta description, a canonical
      link starting with `base_url`, and valid JSON-LD (parsed in the test).
- [ ] `sitemap.xml` lists every page, and `robots.txt` references it.
- [ ] The fixture's `<script>alert(1)</script>` in submitter and notes
      renders escaped on every page, and no JSON-LD block contains a raw
      `</script>`.
- [ ] A Playwright test with JavaScript disabled opens a task page and sees
      the leaderboard.
- [ ] An export with `export_schema: 2`, and an export where one trial row
      breaks the schema, each fail the build with a clear message.
- [ ] `src/lib/types/export.ts` is generated, and CI fails if it is stale.
- [ ] The workflows pass `actionlint` (say in the PR if it was not run).
      Every third-party action is SHA-pinned, and no workflow has write
      permissions beyond the deploy job's.
- [ ] The built site requests nothing from another origin (a Playwright
      test records requests on a task page and on `/compare/`).

## Out of scope

Computing anything from trial data (that is task 4). The compare page
logic (task 5). Custom domains and analytics.
