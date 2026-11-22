"""Amp CLI (``amp``) adapter.

Checked against the official docs (execute mode, streaming JSON, settings,
tools) and against the pinned CLI itself (``--help`` and real output) on
2026-11-21:

* ``amp --execute <prompt>`` runs one prompt non-interactively with the local
  executor. Execute mode is the documented mode for scripts and CI; tools run
  without approval by default, so no permission flag is needed.
* ``--stream-json`` prints Claude Code-compatible JSONL: an ``init`` system
  message, ``user``/``assistant`` messages, and a final ``result``. Assistant
  messages carry per-call ``usage`` (``input_tokens``,
  ``cache_creation_input_tokens``, ``cache_read_input_tokens``,
  ``output_tokens``). The result carries ``subtype``, ``is_error``,
  ``num_turns``, and on failure an ``error``.
* ``--model <provider/name>`` pins one model. ``--mode low|medium|high|ultra``
  selects a routed configuration that controls the model, system prompt and
  tools; a mode is not a model and can use several models. The ``init``
  message's ``agent_mode`` only echoes the requested mode or model, so the
  adapter keeps it in ``raw`` and reports the actual model as unavailable.
* Auth is an access token in ``AMP_API_KEY`` (``sgamp_...`` from Amp Settings,
  or a service account's key). The short-lived session token stored by
  ``amp login`` cannot be refreshed from an environment variable, so there is
  no subscription/login-file mode.
* ``AMP_SKIP_UPDATE_CHECK=1`` keeps the pinned CLI from updating itself.
* Custom endpoints are not supported by this adapter.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ai_benchmark.agents.base import AgentCapabilities, AgentTask
from ai_benchmark.agents.cli import (
    CliAgentAdapter,
    ParsedOutput,
    int_or_none,
    json_lines,
    token_metric,
)
from ai_benchmark.agents.registry import register_agent
from ai_benchmark.config.auth import AgentAuthConfig
from ai_benchmark.config.experiment import EndpointConfig
from ai_benchmark.environment.base import AgentImageLayer, CommandResult
from ai_benchmark.errors import ConfigurationError
from ai_benchmark.models.enums import AuthMode, MetricSource
from ai_benchmark.models.metrics import Metric, TokenUsage, UsageMetrics

AMP_MODES = frozenset({"low", "medium", "high", "ultra"})
"""Built-in Amp modes: routed configurations, not model IDs.

A mode controls the model, system prompt and tool selection, and may route a
run across several models, so the adapter records it as the requested value
and never claims it as the model that answered.
"""

_USAGE_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


@register_agent
class AmpAdapter(CliAgentAdapter):
    name = "amp"
    display_name = "Amp"
    executable = "amp"
    cli_version: ClassVar[str] = "0.0.1795276835-g0feb0c"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            structured_output=True,
            reports_token_usage=True,
            reports_turns=True,
            reports_rate_limits=True,
            supports_reasoning_effort=True,
            supported_auth_modes=(AuthMode.API_KEY,),
            token_accounting_scheme="amp",
        )

    def image_layer(self) -> AgentImageLayer:
        return AgentImageLayer(
            agent=self.name,
            cli_version=self.cli_version,
            requires=["node"],
            install_commands=[
                f"npm install -g --no-audit --no-fund @ampcode/cli@{self.cli_version}"
            ],
            env={"AMP_SKIP_UPDATE_CHECK": "1"},
        )

    def default_auth(self, mode: AuthMode) -> AgentAuthConfig:
        if mode is AuthMode.SUBSCRIPTION:
            raise ConfigurationError(
                "amp is API-key only: set AMP_API_KEY to an access token; the CLI's "
                "saved login is a short-lived session token that cannot run unattended"
            )
        return AgentAuthConfig(mode=mode, env=["AMP_API_KEY"])

    def endpoint_auth(self, endpoint: EndpointConfig) -> AgentAuthConfig:
        raise ConfigurationError("amp does not support custom endpoints")

    def build_command(self, task: AgentTask) -> list[str]:
        if task.endpoint is not None:
            raise ConfigurationError("amp does not support custom endpoints")
        cmd = [
            self.executable,
            "--stream-json",
            "--no-color",
            "--no-notifications",
            "--executor",
            "local",
        ]
        if task.model:
            mode = self._mode(task.model)
            cmd += ["--mode", mode] if mode else ["--model", task.model]
        if task.reasoning_effort:
            cmd += ["--effort", task.reasoning_effort]
        return [*cmd, "--execute", task.prompt]

    @staticmethod
    def _mode(requested: str) -> str | None:
        """The built-in mode named by ``requested``, case-insensitively, else None."""
        candidate = requested.strip().lower()
        return candidate if candidate in AMP_MODES else None

    def reported_failure(self, parsed: ParsedOutput, result: CommandResult) -> bool:
        """Amp prints result errors but still exits 0; the error is the failure signal."""
        return bool(parsed.errors)

    # -- output ---------------------------------------------------------------

    def parse_output(self, stdout: str) -> ParsedOutput:
        raw: dict[str, Any] = {
            "session_id": None,
            "agent_mode": None,
            "assistant_usage": [],
            "tool_calls": 0,
            "tool_errors": 0,
            "errors": [],
            "result": None,
        }
        errors: list[str] = []
        result: dict[str, Any] | None = None
        for event in json_lines(stdout):
            kind = event.get("type")
            if kind == "system":
                if event.get("subtype") == "init":
                    raw["session_id"] = event.get("session_id")
                    raw["agent_mode"] = event.get("agent_mode")
                message = event.get("error")
                if isinstance(message, str) and message:
                    errors.append(message)
            elif kind == "assistant":
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                if isinstance(usage, dict):
                    raw["assistant_usage"].append(usage)
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        raw["tool_calls"] += 1
            elif kind == "user":
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                for block in message.get("content") or []:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        and block.get("is_error")
                    ):
                        raw["tool_errors"] += 1
            elif kind == "result":
                result = event
        if result is not None:
            raw["result"] = result
            raw["session_id"] = result.get("session_id") or raw["session_id"]
            if result.get("is_error") or str(result.get("subtype", "")).startswith("error"):
                message = result.get("error") or result.get("subtype") or "error"
                errors.append(str(message))
        raw["errors"] = errors
        return ParsedOutput(raw=raw, errors=errors)

    def parse_usage(self, raw: dict[str, Any]) -> tuple[UsageMetrics, Metric[str]]:
        raw_result = raw.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        usage = result.get("usage")
        if not isinstance(usage, dict) or not any(
            int_or_none(usage.get(key)) is not None for key in _USAGE_KEYS
        ):
            usage = _sum_assistant_usage(raw.get("assistant_usage") or [])
        fresh = int_or_none(usage.get("input_tokens"))
        written = int_or_none(usage.get("cache_creation_input_tokens")) or 0
        read = int_or_none(usage.get("cache_read_input_tokens"))
        output = int_or_none(usage.get("output_tokens"))
        # Amp normalises every provider to Anthropic-style usage fields: cache
        # writes and reads are counted outside input_tokens, so add them back to
        # keep the platform's "all input tokens, of which cached" meaning.
        total_in = None if fresh is None else fresh + written + (read or 0)
        tokens = TokenUsage(
            accounting_scheme="amp",
            input_tokens=token_metric(total_in, "input + cache writes + cache reads"),
            cached_input_tokens=token_metric(read),
            output_tokens=token_metric(output),
            reasoning_tokens=Metric[int].unavailable(
                "amp stream JSON does not report reasoning tokens", unit="tokens"
            ),
            total_tokens=token_metric(
                None if total_in is None or output is None else total_in + output
            ),
        )
        turns = int_or_none(result.get("num_turns"))
        turns_metric = (
            Metric[int].of(turns, MetricSource.AGENT_REPORTED, note="agent turns")
            if turns is not None
            else Metric[int].unavailable("not reported by the agent")
        )
        return (
            UsageMetrics(
                tokens=tokens,
                cost=Metric[float].unavailable("amp does not report per-run cost", unit="USD"),
                turns=turns_metric,
            ),
            Metric[str].unavailable(
                "Amp routes across models; the CLI only echoes the requested mode or "
                "model and does not report which model(s) served the run"
            ),
        )


def _sum_assistant_usage(entries: Any) -> dict[str, int | None]:
    """Per-key totals over the assistant messages, or None for keys never reported."""
    totals: dict[str, int | None] = {key: None for key in _USAGE_KEYS}
    if not isinstance(entries, list):
        return totals
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key in _USAGE_KEYS:
            value = int_or_none(entry.get(key))
            if value is not None:
                totals[key] = (totals[key] or 0) + value
    return totals
