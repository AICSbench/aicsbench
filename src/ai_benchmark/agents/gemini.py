"""Gemini CLI adapter for API-key authenticated, unattended trials.

The pinned CLI's JSON output has ``stats.models[model].tokens`` with
``prompt`` (including ``cached``), ``candidates``, ``thoughts`` and ``total``.
Gemini does not report billed cost or agentic turn count in that format.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from ai_benchmark.agents.base import AgentCapabilities, AgentTask
from ai_benchmark.agents.cli import CliAgentAdapter, ParsedOutput, int_or_none, token_metric
from ai_benchmark.agents.registry import register_agent
from ai_benchmark.config.auth import AgentAuthConfig
from ai_benchmark.environment.base import AgentImageLayer
from ai_benchmark.errors import ConfigurationError
from ai_benchmark.models.enums import AuthMode, MetricSource
from ai_benchmark.models.metrics import Metric, TokenUsage, UsageMetrics


@register_agent
class GeminiAdapter(CliAgentAdapter):
    name = "gemini"
    display_name = "Gemini CLI"
    executable = "gemini"
    cli_version: ClassVar[str] = "0.68.0"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            structured_output=True,
            reports_token_usage=True,
            reports_rate_limits=True,
            supported_auth_modes=(AuthMode.API_KEY,),
            token_accounting_scheme="google",
        )

    def image_layer(self) -> AgentImageLayer:
        return AgentImageLayer(
            agent=self.name,
            cli_version=self.cli_version,
            requires=["node"],
            install_commands=[
                f"npm install -g --no-audit --no-fund @google/gemini-cli@{self.cli_version}"
            ],
            env={
                "GEMINI_SANDBOX": "false",
                "GEMINI_CLI_TRUST_WORKSPACE": "true",
            },
        )

    def default_auth(self, mode: AuthMode) -> AgentAuthConfig:
        if mode is not AuthMode.API_KEY:
            raise ConfigurationError("Gemini adapter currently requires --auth api_key")
        return AgentAuthConfig(mode=mode, env=["GEMINI_API_KEY"])

    def build_command(self, task: AgentTask) -> list[str]:
        cmd = [
            self.executable,
            "--approval-mode", "yolo",
            "--extensions", "none",
            "--output-format", "json",
        ]
        if task.model:
            cmd += ["--model", task.model]
        return [*cmd, "--prompt", task.prompt]

    def parse_output(self, stdout: str) -> ParsedOutput:
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError:
            return ParsedOutput()
        if not isinstance(value, dict):
            return ParsedOutput()
        error = value.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        return ParsedOutput(raw=value, errors=[str(message)] if message else [])

    def parse_usage(self, raw: dict[str, Any]) -> tuple[UsageMetrics, Metric[str]]:
        stats = raw.get("stats")
        models = stats.get("models") if isinstance(stats, dict) else None
        if not isinstance(models, dict) or not models:
            return UsageMetrics(), Metric[str].unavailable("not reported by the agent")

        sums: dict[str, int] = {}
        seen: set[str] = set()
        for details in models.values():
            tokens = details.get("tokens") if isinstance(details, dict) else None
            if not isinstance(tokens, dict):
                continue
            for key in ("prompt", "cached", "candidates", "thoughts", "total"):
                amount = int_or_none(tokens.get(key))
                if amount is not None:
                    sums[key] = sums.get(key, 0) + amount
                    seen.add(key)

        def get(key: str) -> int | None:
            return sums[key] if key in seen else None

        tokens = TokenUsage(
            accounting_scheme="google",
            input_tokens=token_metric(get("prompt"), "prompt tokens include cached input"),
            cached_input_tokens=token_metric(get("cached")),
            output_tokens=token_metric(get("candidates"), "excludes thinking tokens"),
            reasoning_tokens=token_metric(get("thoughts")),
            total_tokens=token_metric(get("total"), "provider-reported total"),
        )
        def candidate_count(name: str) -> int:
            details = models[name]
            tokens = details.get("tokens") if isinstance(details, dict) else None
            return (int_or_none(tokens.get("candidates")) or 0) if isinstance(tokens, dict) else 0

        main = max(models, key=candidate_count)
        model = Metric[str].of(main, MetricSource.AGENT_REPORTED)
        if len(models) > 1:
            model = Metric[str].of(
                main, MetricSource.AGENT_REPORTED,
                note="main model; also used: " + ", ".join(sorted(m for m in models if m != main)),
            )
        return UsageMetrics(tokens=tokens), model
