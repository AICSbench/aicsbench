"""Command Code's pinned non-interactive JSONL adapter."""

from pathlib import Path
from typing import Any, ClassVar

from ai_benchmark.agents.base import AgentCapabilities, AgentTask
from ai_benchmark.agents.cli import (
    AGENT_HOME,
    CliAgentAdapter,
    ParsedOutput,
    int_or_none,
    json_lines,
    token_metric,
)
from ai_benchmark.agents.registry import register_agent
from ai_benchmark.config.auth import AgentAuthConfig, CredentialFile
from ai_benchmark.config.experiment import EndpointConfig
from ai_benchmark.environment.base import AgentImageLayer
from ai_benchmark.errors import ConfigurationError
from ai_benchmark.models.enums import AuthMode
from ai_benchmark.models.metrics import Metric, TokenUsage, UsageMetrics


@register_agent
class CommandCodeAdapter(CliAgentAdapter):
    name = "commandcode"
    display_name = "Command Code"
    executable = "commandcode"
    cli_version: ClassVar[str] = "1.91.1"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            structured_output=True,
            reports_token_usage=True,
            reports_rate_limits=True,
            supports_reasoning_effort=True,
        )

    def image_layer(self) -> AgentImageLayer:
        return AgentImageLayer(
            agent=self.name,
            cli_version=self.cli_version,
            requires=["node"],
            install_commands=[
                f"npm install -g --no-audit --no-fund command-code@{self.cli_version}"
            ],
            env={"OTEL_SDK_DISABLED": "true"},
        )

    def default_auth(self, mode: AuthMode) -> AgentAuthConfig:
        if mode is AuthMode.SUBSCRIPTION:
            return AgentAuthConfig(
                mode=mode,
                files=[
                    CredentialFile(
                        source=Path("~/.commandcode/auth.json"),
                        target=f"{AGENT_HOME}/.commandcode/auth.json",
                    )
                ],
            )
        return AgentAuthConfig(mode=mode, env=["COMMAND_CODE_API_KEY"])

    def endpoint_auth(self, endpoint: EndpointConfig) -> AgentAuthConfig:
        raise ConfigurationError("commandcode does not support custom endpoints")

    def build_command(self, task: AgentTask) -> list[str]:
        if task.endpoint is not None:
            raise ConfigurationError("commandcode does not support custom endpoints")
        cmd = [
            self.executable,
            "--trust",
            "--yolo",
            "--safe-mode",
            "--no-skills",
            "--no-session",
            "--skip-onboarding",
            "--no-auto-update",
            "--output-format",
            "json",
        ]
        if task.model:
            cmd += ["--model", task.model]
        if task.reasoning_effort:
            cmd += ["--effort", task.reasoning_effort]
        return [*cmd, "--print", task.prompt]

    def parse_output(self, stdout: str) -> ParsedOutput:
        result = next((line for line in json_lines(stdout) if line.get("type") == "result"), {})
        error = result.get("error")
        return ParsedOutput(raw=result, errors=[error] if isinstance(error, str) else [])

    def parse_usage(self, raw: dict[str, Any]) -> tuple[UsageMetrics, Metric[str]]:
        usage = raw.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        inputs = int_or_none(usage.get("inputTokens"))
        outputs = int_or_none(usage.get("outputTokens"))
        cached_inputs = int_or_none(usage.get("cacheReadTokens"))
        # An early failure can report all zeros without having run a model.
        if raw.get("subtype") not in {"success", "max_turns"} and not any(
            count is not None and count > 0 for count in (inputs, outputs, cached_inputs)
        ):
            inputs = outputs = cached_inputs = None
        return (
            UsageMetrics(
                tokens=TokenUsage(
                    input_tokens=token_metric(inputs),
                    cached_input_tokens=token_metric(cached_inputs),
                    output_tokens=token_metric(outputs),
                    total_tokens=token_metric(
                        None if inputs is None or outputs is None else inputs + outputs
                    ),
                )
            ),
            Metric[str].unavailable("Command Code's final result does not report a model"),
        )
