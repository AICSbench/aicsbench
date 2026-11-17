# Task 1: harness fields and dimensions

**Branch:** `feat/phase5-harness-dimensions` · **Depends on:** nothing ·
**Commit scope:** `models`, `cli`

## Why

The public site compares harnesses (agent CLIs: Claude Code, Codex,
OpenCode) and harness versions, not only models. Trials already record the
harness as `agent.name` and its version as `agent.version`, but the
comparison dimensions, exports and dashboard cannot group by version, and
nothing calls it a harness. See [decisions.md](../decisions.md) D8 and the
"Harness in code" default.

## Changes

1. `src/ai_benchmark/models/enums.py`, `ComparisonDimension`: add
   - `HARNESS = "harness"` → `agent.name` (same grouping as `AGENT`, the
     user-facing name)
   - `HARNESS_VERSION = "harness_version"` → `f"{agent.name} {agent.version or 'unknown'}"`
   - `HARNESS_MODEL = "harness_model"` → same value as `AGENT_MODEL`
   Keep `AGENT` and `AGENT_MODEL` working; existing CLI flags and stored
   results must not break.
2. `src/ai_benchmark/models/results.py`, `TrialResult.dimension_value`: map
   the three new members.
3. **One model across harnesses.** The same model has different labels per
   harness: Codex records `gpt-6-luna`, OpenCode records
   `openai/gpt-6-luna` (OpenCode uses `provider/model` and does not report
   the model). Today `--by agent` refuses that pair as "mixes models". Add
   `AgentRecord.model_key` (a property, not stored): `model_label` in lower
   case with a leading `<provider>/` removed. Use `model_key` wherever a
   rule asks for "one model" across harnesses (`AGENT`, `HARNESS`,
   `HARNESS_VERSION`) and for the site's harness pages. `MODEL` and
   `AGENT_MODEL` grouping keep using `model_label`, so existing output is
   unchanged.
4. `src/ai_benchmark/comparison.py`, `compare_trials`: it refuses
   comparisons that leave another dimension mixed. Extend those rules:
   - `HARNESS` behaves like `AGENT` (one model, one stack).
   - `HARNESS_MODEL` behaves like `AGENT_MODEL` (one stack).
   - `HARNESS_VERSION` needs one harness, one model and one stack, with the
     message `"harness version comparison mixes harnesses, models or stacks;
     filter to one of each"`.
5. `src/ai_benchmark/reporting.py`, `trial_row`: add `harness`,
   `harness_version`, `requested_model`, `reasoning_effort` (see
   [schemas.md](../schemas.md#datatrialsjson)). Existing keys keep their
   names, order and meaning; new keys go at the end.
6. `src/ai_benchmark/storage/base.py`, `ResultFilter`: add
   `harness_version: str | None = None`, applied in `JsonResultStore`.
7. CLI: `aicsbench compare --by harness|harness_version|harness_model` works
   (it already takes the enum); `aicsbench results` gets
   `--harness-version`. Help texts say "harness (agent CLI)".
8. Dashboard (`dashboard.py`): add the three dimensions to the "Compare by"
   select and a "Harness version" filter.
9. Docs: README command list and `docs/agents.md` mention that the agent
   adapter name is called the harness in reports.

## Acceptance criteria

- [ ] `aicsbench compare --run R --by harness_version` groups trials of the
      same harness by version, and shows `codex unknown` for a trial with no
      version.
- [ ] `aicsbench export --format csv` has the four new columns at the end;
      a stored trial from before this change still exports (missing values
      are empty).
- [ ] A run with `codex:gpt-6-luna` and `opencode:openai/gpt-6-luna` on one
      stack compares with `--by harness` (and `--by agent`) without the
      "mixes models" refusal; `codex:gpt-6-luna` with
      `opencode:openai/gpt-5` is still refused.
- [ ] `ResultFilter(harness_version="0.167.2")` returns only trials with
      that `agent.version`.
- [ ] The dashboard offers the new dimensions and filter, and
      `tests/test_dashboard.py` covers one of them.
- [ ] Unit tests for every new enum member in `dimension_value` and in the
      `compare_trials` refusal rules, the new export columns, and the filter.
- [ ] No change to stored result files or `comparability_key`.

## Out of scope

Bundles, validation, the site. Renaming `agent` fields in code.
