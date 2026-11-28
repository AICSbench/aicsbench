"""OpenAI Codex CLI (``codex``) adapter.

Checked against the official docs (non-interactive mode, auth, config
reference) and the pinned CLI's own ``codex exec --help``:

* ``codex exec --json`` prints JSONL events: ``thread.started``,
  ``turn.started``, ``item.*``, ``turn.completed`` (with ``usage``:
  ``input_tokens``, ``cached_input_tokens``, ``output_tokens``,
  ``reasoning_output_tokens``), ``turn.failed`` and ``error``.
* ``--dangerously-bypass-approvals-and-sandbox`` is the documented mode for
  "environments that are externally sandboxed", which the trial container is.
  Codex's own sandbox would also need kernel features containers often lack.
* Reasoning effort is the ``model_reasoning_effort`` config key.
* Subscription auth: ``~/.codex/auth.json`` from ``codex login`` (ChatGPT
  plan). API auth: ``CODEX_API_KEY``, which ``codex exec`` reads directly.
* Codex reports neither cost nor the model it used.
* Custom endpoint (task 9): a ``model_providers.bench`` entry passed as
  ``--config`` overrides (``model_provider``, ``base_url``, ``env_key``, and
  ``wire_api`` when set), checked against
  https://developers.openai.com/codex/config-advanced and
  https://developers.openai.com/codex/config-reference. ``env_key`` names the
  environment variable Codex reads the gateway key from, so the key's value
  never appears in argv or files. Note the pinned CLI only speaks the
  Responses API: since v0.159.2 ``wire_api = "chat"`` is rejected at config
  load (upstream removed it; see
  https://github.com/openai/codex/discussions/7782), so a ``chat`` endpoint
  fails at CLI startup, not here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from aicsbench.agents.base import AgentCapabilities, AgentTask
from aicsbench.agents.cli import (
    AGENT_HOME,
    CliAgentAdapter,
    ParsedOutput,
    int_or_none,
    json_lines,
    token_metric,
)
from aicsbench.agents.registry import register_agent
from aicsbench.config.auth import AgentAuthConfig, CredentialFile
from aicsbench.environment.base import AgentImageLayer
from aicsbench.models.enums import AuthMode
from aicsbench.models.metrics import Metric, TokenUsage, UsageMetrics


@register_agent
class CodexAdapter(CliAgentAdapter):
    name = "codex"
    display_name = "Codex CLI"
    executable = "codex"
    cli_version: ClassVar[str] = "0.167.2"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            structured_output=True,
            reports_token_usage=True,
            reports_rate_limits=True,
            supports_reasoning_effort=True,
            token_accounting_scheme="openai",
        )

    def image_layer(self) -> AgentImageLayer:
        return AgentImageLayer(
            agent=self.name,
            cli_version=self.cli_version,
            requires=["node"],
            install_commands=[
                f"npm install -g --no-audit --no-fund @openai/codex@{self.cli_version}"
            ],
        )

    def default_auth(self, mode: AuthMode) -> AgentAuthConfig:
        if mode is AuthMode.SUBSCRIPTION:
            return AgentAuthConfig(
                mode=mode,
                files=[
                    CredentialFile(
                        source=Path("~/.codex/auth.json"),
                        target=f"{AGENT_HOME}/.codex/auth.json",
                    )
                ],
            )
        return AgentAuthConfig(mode=mode, env=["CODEX_API_KEY"])

    def build_command(self, task: AgentTask) -> list[str]:
        cmd = [
            self.executable,
            "exec",
            "--json",
            "--color",
            "never",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--cd",
            task.workdir,
        ]
        if task.model:
            cmd += ["--model", task.model]
        if task.reasoning_effort:
            cmd += ["--config", f'model_reasoning_effort="{task.reasoning_effort}"']
        if task.endpoint is not None:
            endpoint = task.endpoint
            # Codex 0.167.2 defaults model_providers.<id>.name to "" and
            # rejects that during configuration loading, so set it explicitly.
            cmd += [
                "--config", 'model_provider="bench"',
                "--config", 'model_providers.bench.name="bench"',
                "--config", f'model_providers.bench.base_url="{endpoint.base_url}"',
                "--config", f'model_providers.bench.env_key="{endpoint.api_key_env}"',
            ]  # fmt: skip
            if endpoint.wire_api is not None:
                cmd += ["--config", f'model_providers.bench.wire_api="{endpoint.wire_api}"']
        return [*cmd, "--", task.prompt]

    def validate_options(self, options: dict[str, Any]) -> None:
        if options:
            raise ValueError(f"codex takes no options, got {sorted(options)}")

    # -- output ---------------------------------------------------------------

    def parse_output(self, stdout: str) -> ParsedOutput:
        thread_id = None
        usages: list[dict[str, Any]] = []
        errors: list[str] = []
        final_message = None
        for event in json_lines(stdout):
            kind = event.get("type")
            if kind == "thread.started":
                thread_id = event.get("thread_id")
            elif kind == "turn.completed" and isinstance(event.get("usage"), dict):
                usages.append(event["usage"])
            elif kind == "turn.failed":
                err = event.get("error")
                errors.append(str(err.get("message") if isinstance(err, dict) else err))
            elif kind == "error":
                errors.append(str(event.get("message") or event))
            elif kind == "item.completed":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    final_message = item.get("text")
        raw = {
            "thread_id": thread_id,
            "turn_usage": usages,
            "errors": errors,
            "final_message": final_message,
        }
        return ParsedOutput(raw=raw, errors=errors)

    def parse_usage(self, raw: dict[str, Any]) -> tuple[UsageMetrics, Metric[str]]:
        usages = [u for u in raw.get("turn_usage") or [] if isinstance(u, dict)]

        def total(key: str) -> int | None:
            values = [int_or_none(u.get(key)) for u in usages]
            present = [v for v in values if v is not None]
            return sum(present) if present else None

        inp, out = total("input_tokens"), total("output_tokens")
        tokens = TokenUsage(
            accounting_scheme="openai",
            input_tokens=token_metric(inp, "includes cached input"),
            cached_input_tokens=token_metric(total("cached_input_tokens")),
            output_tokens=token_metric(out, "includes reasoning output"),
            reasoning_tokens=token_metric(total("reasoning_output_tokens")),
            total_tokens=token_metric(None if inp is None or out is None else inp + out),
        )
        usage = UsageMetrics(
            tokens=tokens,
            cost=Metric[float].unavailable("codex does not report cost", unit="USD"),
            # Codex "turns" are user turns (one per exec), not agentic steps.
            turns=Metric[int].unavailable("codex does not report agentic turns"),
        )
        return usage, Metric[str].unavailable("codex exec does not report the model used")
