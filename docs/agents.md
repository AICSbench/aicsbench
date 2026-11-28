# Built-in agent adapters

Each adapter installs a **pinned** CLI version into the agent image layer (on
the platform's pinned Node.js where the CLI needs it) and runs it once per
trial, non-interactively, in the workspace. Flags and output formats below were
checked against the official docs and against the pinned CLI itself (`--help`
and its output) on 2026-11-13 (Amp on 2026-11-21, `agy` on 2026-11-22). Bumping
a version means re-checking this page.

In reports and comparisons the adapter name (`claude`, `codex`, `commandcode`,
`agy`, `opencode`, `amp`) is the **harness**, and its pinned CLI version is the
**harness version**.

| | Claude Code | Codex CLI | OpenCode | Amp |
| --- | --- | --- | --- | --- |
| Adapter name | `claude` | `codex` | `opencode` | `amp` |
| Pinned version | `@anthropic-ai/claude-code@2.1.323` | `@openai/codex@0.167.2` | `opencode-ai@1.19.5` | `@ampcode/cli@0.0.1795276835-g0feb0c` |
| Command | `claude --print --output-format json --permission-mode bypassPermissions --strict-mcp-config` | `codex exec --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox --cd <workdir>` | `opencode run --format json --auto` | `amp --stream-json --executor local --execute <prompt>` |
| Model | `--model <alias or id>` | `--model <id>` | `--model <provider>/<model>` (required form) | `--model <provider>/<model>`, or `--mode low\|medium\|high\|ultra` |
| Reasoning effort | `--effort low\|medium\|high\|xhigh\|max` | `--config model_reasoning_effort="<level>"` | `--variant <variant>` (provider-specific) | `--effort <level>` |
| Output | one JSON result object | JSONL events | JSONL events | JSONL events |
| Updates off | `DISABLE_AUTOUPDATER`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | pinned install | `OPENCODE_DISABLE_AUTOUPDATE` | `AMP_SKIP_UPDATE_CHECK` |

Permission prompts are turned off in every CLI: the trial container is the
execution boundary and the agent runs as a non-root user. It shares a per-trial
network with its services and can make outbound HTTPS requests.

## What each agent reports

| Metric | Claude Code | Codex CLI | OpenCode | Amp |
| --- | --- | --- | --- | --- |
| Input / cached / output tokens | yes | yes | yes, summed over model steps | yes, summed over assistant messages |
| Reasoning tokens | yes (`thinking_tokens`) | yes (`reasoning_output_tokens`) | yes | **unavailable** |
| Cost | yes (`total_cost_usd`) | **unavailable** | yes, summed over steps | **unavailable** |
| Turns | agentic turns (`num_turns`) | **unavailable** | model steps | agent turns (`num_turns`) |
| Model actually used | yes, from `modelUsage` (main model; helpers listed in the note) | **unavailable** | **unavailable** | **unavailable**: a mode routes across models, and the init message only echoes the requested mode or model |
| Usage / rate limits | result text such as "Usage limit reached" | `error` / `turn.failed` such as "You've hit your usage limit" | `error` events, HTTP 429 | result errors such as "Out of credits" or "Rate limit exceeded" |

Notes:

- **Tokens are normalised** to "all input tokens, of which cached". Anthropic,
  Amp and OpenCode count cache reads and writes outside `input_tokens`, so the
  adapters add them back; Codex already includes cached tokens. Counts are
  still only comparable within one `accounting_scheme`.
- **Cost under a subscription** is always recorded as unavailable by the
  orchestrator; a figure the CLI prints anyway is kept separately.
- Anything an agent does not report is **unavailable**, never zero.
- Raw stdout and stderr are kept per trial in `logs/agent.stdout.log` and
  `logs/agent.stderr.log`; the stored command line replaces the prompt with
  `<prompt>`.

## Command Code

Select `commandcode` (or `commandcode:<model>` to pin the requested model).
The trial image installs `command-code@1.91.1` and runs the CLI headlessly
with JSONL output. Subscription runs use `~/.commandcode/auth.json` after
`commandcode login`; API-key runs use `COMMAND_CODE_API_KEY`. Custom endpoints
are not supported by this adapter.

The adapter reports input, cached input, and output tokens from the final
result. Cost, reasoning tokens, turn count, and the actual model are left
unavailable. Errors before inference do not turn missing metrics into zeros.

## Amp

Select `amp` with a model (`amp:<provider>/<model>`) or a built-in mode
(`amp:low`, `amp:medium`, `amp:high`, `amp:ultra`). The trial image installs
`@ampcode/cli@0.0.1795276835-g0feb0c` and runs it in local execute mode with
`--stream-json`. Models use Amp's required `provider/model` form; a bare name
goes to `--model` and is rejected by the CLI. A mode controls the model, system
prompt and tool selection, and can route a run across several models, so it is
recorded as the requested value and the actual model stays unavailable. The
init message's `agent_mode` (which only echoes the request) is kept in the raw
result for inspection.

Auth is API-key only: set `AMP_API_KEY` to an access token from Amp Settings
(`sgamp_...`, or a service account's key) and run with `--auth api_key`. The
CLI's `amp login` token is a short-lived session token that cannot be refreshed
from an environment variable, so there is no subscription or login-file mode.
Custom endpoints are not supported. `AMP_SKIP_UPDATE_CHECK=1` keeps the pinned
CLI from updating itself.

The adapter reports input, cached input, output and total tokens, and
`num_turns`; cost and reasoning tokens are unavailable. Input tokens are
normalised to "all input tokens, of which cached", like the Anthropic adapter.
Rate limits and exhausted credits in the result are flagged. The pinned CLI
exits 0 for a result error (for example an unknown model), so the error text is
recorded on the trial and the trial then fails on its acceptance tests.

## Google Antigravity (`agy`)

Select `agy` (or `agy:<model>` to pin the requested model). The trial image
installs the official Linux CLI **1.5.14** from the published tarball, verifies
its SHA-512 against the release manifest before extracting it, and runs it
headlessly with `--print` and `--output-format stream-json`. The pinned build
supports Linux x86_64 and ARM64 (glibc); any other architecture fails the image
build. The binary is root-owned in `/usr/local/bin`, outside the agent user's
home, so a background self-update cannot replace the pinned build.

API-key auth only. Put `GEMINI_API_KEY` in the host environment (the config
names the variable, never the value) and run with `auth_mode: api_key`. The
image writes `modelProvider: gemini` to
`/home/agent/.gemini/antigravity-cli/settings.json`; the key is injected per
trial and never baked into the image or exported.

**Subscription/account auth is blocked in the isolated trial container.** The
official headless flow uses credentials cached by an interactive `agy`
sign-in; on Linux the CLI reads them from the OS keyring (Secret Service over
D-Bus), and the docs say to authenticate once interactively before a headless
run. A trial container is fresh, non-interactive and does not mount the host
keyring or home directory. Pinned 1.5.14 detects the container and switches to
its file-based token store, but that store is not read back by a fresh process
in a container (upstream issue #479, open), and a container-local keyring
transplant is not accepted (upstream issue #223 comments; the subscription
headless request #1194 is still open). Copying `antigravity-oauth-token` or the
identity state therefore does not authenticate a fresh trial. The adapter
rejects `subscription` at preflight and never falls back to API-key billing;
use `auth_mode: api_key` with `GEMINI_API_KEY`. If Google publishes a supported
headless OAuth/token flow, add it then.

Choose a model with `agy models`; the slugs include the effort level, for
example `gemini-3.8-flash-medium`. `reasoning_effort` maps to `--effort`
(`low|medium|high|xhigh|max`); the CLI rejects a level the model does not
support, and an unknown `--model` exits non-zero with status `ERROR` instead of
falling back. See `examples/agy-api-key.yaml`.

The standard setup pins the built-in `default` agent, disables slash-command
and skill expansion, denies MCP tools (`mcp(*)`), and installs no plugins or
custom agents. `--dangerously-skip-permissions` auto-approves tool calls
because the trial container is the isolation boundary: the CLI runs as a
non-root user with no Docker socket, a per-trial workspace and network, and
credentials injected per trial. The runner enforces the task timeout; the
CLI's `--print-timeout` is left unlimited.

| Metric | Antigravity |
| --- | --- |
| Input / cached / output tokens | yes, from the terminal `result.usage` |
| Reasoning tokens | yes (`thinking_tokens`, reported separately) |
| Cost | **unavailable** |
| Turns | `num_turns` (user turns, not agentic turns) |
| Model actually used | **unavailable** (the `init` event echoes the requested model) |
| Usage / rate limits | terminal `status` + `error`; structured `AGY_ERROR` (HTTP 429 / `RESOURCE_EXHAUSTED`) |

Token accounting: `total_tokens` is the CLI's reported total; in the pinned
samples it is `input_tokens + output_tokens`. Cache reads are included in
`input_tokens`, and thinking tokens are reported separately without being
added to the total. Counts are recorded under the `gemini` accounting scheme.
A failed run before inference reports a zeroed usage object; the adapter
records that as unavailable, not as zero usage.

## Not yet supported

- MCP servers (`best_ecosystem` tooling). Claude Code runs with
  `--strict-mcp-config`, so no MCP server is loaded in any mode yet. `agy`
  denies all MCP tools with `mcp(*)`.
- Antigravity subscription/account auth: blocked because a fresh trial
  container has no supported headless credential path (see above). Custom
  endpoints and remote agents are also out of scope; this adapter is API-key
  only.
- Checking credentials before a trial: it would cost a model call, so a bad
  credential shows up as an `agent_error` on the first trial.
