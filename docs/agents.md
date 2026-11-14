# Built-in agent adapters

Each adapter installs a **pinned** CLI version into the agent image layer (on
the platform's pinned Node.js) and runs it once per trial, non-interactively,
in the workspace. Flags and output formats below were checked against the
official docs and against the pinned CLI itself (`--help` and its output) on
2026-11-13 for the original three adapters. Gemini's flags and JSON schema
were checked against its official docs and source on 2026-11-14; its API key
flow has not been run against a live account. Bumping a version means re-checking this page.

| | Claude Code | Codex CLI | OpenCode | Gemini CLI |
| --- | --- | --- | --- | --- |
| Adapter name | `claude` | `codex` | `opencode` | `gemini` |
| Pinned version | `@anthropic-ai/claude-code@2.1.323` | `@openai/codex@0.167.2` | `opencode-ai@1.19.5` | `@google/gemini-cli@0.68.0` |
| Command | `claude --print --output-format json --permission-mode bypassPermissions --strict-mcp-config` | `codex exec --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox --cd <workdir>` | `opencode run --format json --auto` | `gemini --approval-mode yolo --extensions none --output-format json --prompt <prompt>` |
| Model | `--model <alias or id>` | `--model <id>` | `--model <provider>/<model>` (required form) | `--model <id>` |
| Reasoning effort | `--effort low\|medium\|high\|xhigh\|max` | `--config model_reasoning_effort="<level>"` | `--variant <variant>` (provider-specific) | unavailable |
| Output | one JSON result object | JSONL events | JSONL events | one JSON result object |
| Updates off | `DISABLE_AUTOUPDATER`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | pinned install | `OPENCODE_DISABLE_AUTOUPDATE` | pinned install |

Permission prompts are turned off in every CLI: the trial container is the
execution boundary and the agent runs as a non-root user. It shares a per-trial
network with its services and can make outbound HTTPS requests.
Gemini uses `GEMINI_SANDBOX=false` to avoid starting a nested container and
`GEMINI_CLI_TRUST_WORKSPACE=true` for unattended operation.

Gemini currently supports API key authentication only. Set `GEMINI_API_KEY`
on the host and run, for example:

```bash
aicsbench run --agents gemini:<model-id> --task task-management-api \
  --stacks fastapi --trials 1 --auth api_key
```

## What each agent reports

| Metric | Claude Code | Codex CLI | OpenCode | Gemini CLI |
| --- | --- | --- | --- | --- |
| Input / cached / output tokens | yes | yes | yes, summed over model steps | yes, summed per model |
| Reasoning tokens | yes (`thinking_tokens`) | yes (`reasoning_output_tokens`) | yes | yes (`thoughts`) |
| Cost | yes (`total_cost_usd`) | **unavailable** | yes, summed over steps | **unavailable** |
| Turns | agentic turns (`num_turns`) | **unavailable** | model steps | **unavailable** |
| Model actually used | yes, from `modelUsage` (main model; helpers listed in the note) | **unavailable** | **unavailable** | yes, main model by candidate tokens |
| Usage / rate limits | result text such as "Usage limit reached" | `error` / `turn.failed` such as "You've hit your usage limit" | `error` events, HTTP 429 | JSON `error.message` and stderr |

Notes:

- **Tokens are normalised** to "all input tokens, of which cached". Anthropic
  and OpenCode count cache reads and writes outside `input_tokens`, so the
  adapters add them back; Codex already includes cached tokens. Counts are
  still only comparable within one `accounting_scheme`.
- Gemini reports `prompt` tokens including cached input, `candidates` tokens
  excluding thinking, and a separate provider `total`. These fields may not
  add up as they do for other agents.
- **Cost under a subscription** is always recorded as unavailable by the
  orchestrator; a figure the CLI prints anyway is kept separately.
- Anything an agent does not report is **unavailable**, never zero.
- Raw stdout and stderr are kept per trial in `logs/agent.stdout.log` and
  `logs/agent.stderr.log`; the stored command line replaces the prompt with
  `<prompt>`.

## Not yet supported

- MCP servers (`best_ecosystem` tooling). Claude Code runs with
  `--strict-mcp-config`, so no MCP server is loaded in any mode yet.
- Checking credentials before a trial: it would cost a model call, so a bad
  credential shows up as an `agent_error` on the first trial.
