# Task 9: custom model endpoints (one model on every harness)

**Branch:** `feat/phase5-custom-endpoints` · **Depends on:** task 1 ·
**Commit scope:** `agents`, `config`, `models`, `comparison`

## Why

A harness comparison is only fair when every harness runs the same model.
Claude Code runs only Claude models and Codex runs only OpenAI models,
unless they are pointed at a compatible endpoint. Claude Code reads
`ANTHROPIC_BASE_URL`. Codex takes a custom `model_providers` entry. OpenCode
takes a provider `baseURL`. With a gateway such as OpenRouter or LiteLLM,
all three harnesses can run one model.

Today this only half works. `auth.env` can pass a host variable such as
`ANTHROPIC_BASE_URL` into the container, and Codex's config could be
mounted with `auth.files`. But nothing records that the run went through a
gateway. The result then looks like a direct run, Claude Code computes a
cost from Anthropic prices for a model it did not run, and the run can be
compared with direct runs.

## Changes

1. **Config.** `AgentSelection` gets `endpoint: EndpointConfig | None`:
   - `provider: str`: a slug naming the gateway (`openrouter`, `litellm`).
     This is the value results and comparisons use.
   - `base_url: str`: an `https://` URL (plain `http://` is allowed only for
     `localhost`, a private IP or a Docker service name). It must have no
     user info and no query string, so it cannot carry a key.
   - `api_key_env: str`: the **name** of the host variable holding the key,
     validated like `AgentAuthConfig.env`.
   - `wire_api: "chat" | "responses" | None`: Codex only. Ignored by the
     other adapters.

   An endpoint requires `auth.mode: api_key`. `subscription` with an
   endpoint is a config error. The CLI form is the experiment file only. No
   new `--agents` syntax is needed.
2. **Adapters** map the endpoint inside the container. Each one must pass
   the key by variable name and never write its value to argv, files or
   logs:
   - `claude`: env `ANTHROPIC_BASE_URL=<base_url>` and
     `ANTHROPIC_AUTH_TOKEN` taken from `api_key_env`.
   - `codex`: `--config model_provider="bench"`,
     `--config model_providers.bench.base_url="<base_url>"`,
     `--config model_providers.bench.env_key="<api_key_env>"`, plus
     `wire_api` when set.
   - `opencode`: a provider entry for `<provider>` with
     `options.baseURL=<base_url>` in the generated config, and the key
     variable as its API key. Models are written `<provider>/<model>` as
     usual.

   Check each mapping against the CLI's current docs and note the doc URL
   in the adapter's module docstring, as the existing adapters do.
3. **Results.** `AgentRecord` gets `provider: str | None` and
   `endpoint_host: str | None`:
   - With an endpoint, `provider` is the configured slug and
     `endpoint_host` is the URL's host name only (no scheme, port, path).
   - Without one, `provider` is the harness default: `anthropic` for
     claude, `openai` for codex, and for opencode the model's
     `<provider>/` prefix.

   Stored trials without these fields still load, with `provider` set to
   `unknown`: before this task a gateway could be configured through
   `auth.env` without being recorded, so the default cannot be assumed.
   The site labels it "provider not recorded". Two `unknown` trials may
   have used different gateways, so harness comparisons refuse `unknown`
   altogether (item 5). Legacy trials still appear in model and stack
   comparisons.
4. **Cost.** When an endpoint is set, `cost_usd` is
   `unavailable` with the note "custom endpoint; harness price table does
   not apply", unless the gateway's own cost is parsed, which is out of scope
   here.
5. **Comparability.**
   - Add `ComparisonDimension.PROVIDER`.
   - `compare_trials`: a comparison that isolates the harness (`AGENT`,
     `HARNESS`, `HARNESS_VERSION`, alone or with dimensions other than
     `MODEL`) also requires **one provider** across the selection, with the
     message `"harness comparison mixes providers; filter to one provider"`.
     A Codex run against OpenAI directly is never ranked against a Claude
     Code run through OpenRouter.
   - A comparison of harness + model **packages** (`HARNESS_MODEL`,
     `AGENT_MODEL`, or `HARNESS`/`AGENT` together with `MODEL`) ranks each
     pair as it was used, so its rows may differ in provider: Claude Code +
     Opus on Anthropic against Codex + GPT on OpenAI is a valid leaderboard.
     Each row still holds **one provider**: a direct run and a gateway run
     of the same harness and model are never pooled, and such a row refuses
     with the same message. Stack, model key, comparability key and suite
     rules are unchanged.
   - That provider must be known. If any selected trial has provider
     `unknown`, including a selection where every trial is `unknown`, the
     harness rules refuse with `"harness comparison needs a recorded
     provider; exclude trials recorded before provider tracking"`. This also
     applies to harness + model packages. The site's harness pages and the
     compare page apply the same rules.
   - `--by provider` needs one harness, one model key and one stack.
   - `comparability_key` is unchanged.
6. **Export and site.** `trial_row` gets `provider` and `endpoint_host` at
   the end. In the submission bundle, `endpoint_host` is kept only when it
   is in a public allow list (`openrouter.ai`, `api.anthropic.com`,
   `api.openai.com`); otherwise `submit` replaces it with `custom` so a
   private gateway address is never published. The validator rejects a
   bundle whose `endpoint_host` is neither allowed nor `custom`. The site
   shows the provider next to the harness and has a provider filter.
7. **Methodology note.** The site's methodology page states that gateway
   runs include gateway latency in agent time, and that a gateway may route
   or quantize a model differently from its vendor.

## Acceptance criteria

- [ ] An experiment with `claude`, `codex` and `opencode` all on one model
      through one `endpoint` produces trials with the same `provider` and
      `model_key`, and `aicsbench compare --by harness` accepts them.
- [ ] Mixing a direct `codex:gpt-6-luna` trial with an OpenRouter one under
      `--by harness` is refused with the provider message.
- [ ] With fake runners, each adapter's command and env contain the base URL
      and the key's **variable name**. A test sets the key to a marker value
      and asserts the marker appears in no argv, no generated file, no log
      line and no result JSON.
- [ ] `base_url` with user info or a query string, `http://` to a public
      host, and `endpoint` with `subscription` auth are each config errors.
- [ ] A stored trial without `provider` loads as `unknown`, and
      `--by harness` refuses to mix it with a trial whose provider is
      `openai`.
- [ ] `--by harness` over trials that are all `unknown` is refused with the
      recorded-provider message, while `--by stack` over the same trials
      succeeds.
- [ ] `submit` writes `custom` for a private gateway host, and
      `validate-submission` rejects an arbitrary host.

## Out of scope

Parsing gateway cost reports. Running a gateway inside the benchmark.
