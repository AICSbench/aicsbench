"""OpenCode (``opencode``) adapter.

Checked against the official docs (opencode.ai/docs: cli, providers, config,
permissions) and the pinned CLI's own ``opencode run --help`` and source:

* ``opencode run --format json`` prints JSONL events
  ``{"type", "timestamp", "sessionID", ...}``. ``step_finish`` events carry a
  ``part`` with ``tokens`` (``input``, ``output``, ``reasoning``,
  ``cache.read``, ``cache.write``) and ``cost`` per model step; ``error``
  events carry ``error`` (``name``, ``data.message``).
* ``--auto`` auto-approves every permission that is not explicitly denied.
* Models are ``provider/model``; ``--variant`` is the provider's reasoning
  effort (e.g. high, max).
* Subscription auth: ``~/.local/share/opencode/auth.json`` from
  ``opencode auth login``. API auth depends on the provider; the variable
  name comes from the ``api_key_env`` option (default ``ANTHROPIC_API_KEY``)
  or the experiment's ``auth`` block.
* OpenCode does not report which model actually answered.
* Custom endpoint (task 9): a provider entry registered under the internal ID
  ``bench`` with ``options.baseURL``, delivered as ``OPENCODE_CONFIG_CONTENT``
  inline config, checked against https://opencode.ai/docs/providers and
  https://opencode.ai/docs/config. The internal ID keeps OpenCode from
  activating a built-in loader for a well-known slug, and inline config is
  merged after every file-based source, so a workspace ``opencode.json`` or
  ``.opencode`` config cannot override the endpoint's ``baseURL``. The
  command's model prefix is rewritten to the internal ID; the API key is set
  as ``options.apiKey: "{env:<name>}"`` (OpenCode's variable substitution),
  so the generated config carries the key's variable name, never its value.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from aicsbench.agents.base import AgentCapabilities, AgentTask
from aicsbench.agents.cli import (
    AGENT_HOME,
    CliAgentAdapter,
    ParsedOutput,
    json_lines,
    token_metric,
)
from aicsbench.agents.registry import register_agent
from aicsbench.config.auth import AgentAuthConfig, CredentialFile
from aicsbench.environment.base import AgentImageLayer
from aicsbench.errors import ConfigurationError
from aicsbench.models.enums import AuthMode, MetricSource
from aicsbench.models.metrics import Metric, TokenUsage, UsageMetrics

_OPTIONS = {"api_key_env"}

_OPENCODE_EFFORTS = ("low", "medium", "high")
"""Reasoning efforts the generated config declares for an endpoint model.

Matches OpenCode's own variant set for ``@ai-sdk/openai-compatible`` models
(``reasoningEffort`` request options); an effort outside this set is rejected
before the trial starts rather than silently having no effect.
"""

OPENCODE_PROVIDER_ID = "bench"
"""Internal provider ID the generated config registers for a custom endpoint.

A dedicated ID keeps OpenCode from activating a built-in provider loader for a
well-known slug: OpenCode 1.19.5 maps the provider ID ``openai`` to its
built-in ``@ai-sdk/openai`` loader, whose ``responses()`` SDK the
``@ai-sdk/openai-compatible`` package configured here does not provide, so the
run would fail before inference. ``endpoint.provider`` stays the recorded
comparison label; only the command's model prefix is rewritten to this ID.
"""

OPENCODE_CONFIG_CONTENT = "OPENCODE_CONFIG_CONTENT"
"""Env var carrying the generated provider config as inline JSON.

OpenCode merges it after every file-based source - global, ``OPENCODE_CONFIG``,
project ``opencode.json`` files and ``.opencode`` directories - so a workspace
config cannot override the endpoint's ``baseURL`` (``OPENCODE_DISABLE_PROJECT_CONFIG``
does not cover ``.opencode`` directories). OpenCode 1.19.5's config loader reads
it last among user-controllable sources; the container has no managed config.
"""


@register_agent
class OpenCodeAdapter(CliAgentAdapter):
    name = "opencode"
    display_name = "OpenCode"
    executable = "opencode"
    cli_version: ClassVar[str] = "1.19.5"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            structured_output=True,
            reports_token_usage=True,
            reports_cost=True,
            reports_turns=True,
            supports_reasoning_effort=True,
        )

    def image_layer(self) -> AgentImageLayer:
        return AgentImageLayer(
            agent=self.name,
            cli_version=self.cli_version,
            requires=["node"],
            install_commands=[
                f"npm install -g --no-audit --no-fund opencode-ai@{self.cli_version}"
            ],
            env={"OPENCODE_DISABLE_AUTOUPDATE": "1"},
        )

    def default_auth(self, mode: AuthMode) -> AgentAuthConfig:
        if mode is AuthMode.SUBSCRIPTION:
            return AgentAuthConfig(
                mode=mode,
                files=[
                    CredentialFile(
                        source=Path("~/.local/share/opencode/auth.json"),
                        target=f"{AGENT_HOME}/.local/share/opencode/auth.json",
                    )
                ],
            )
        return AgentAuthConfig(
            mode=mode, env=[str(self.options.get("api_key_env", "ANTHROPIC_API_KEY"))]
        )

    def build_command(self, task: AgentTask) -> list[str]:
        cmd = [self.executable, "run", "--format", "json", "--auto"]
        if task.model:
            cmd += ["--model", self.command_model(task)]
        if task.reasoning_effort:
            cmd += ["--variant", task.reasoning_effort]
        return [*cmd, "--", task.prompt]

    def command_model(self, task: AgentTask) -> str:
        """The model ID for ``--model``: the request as-is, or the internal provider.

        Endpoint trials route through :data:`OPENCODE_PROVIDER_ID`, the provider
        the generated config registers; the recorded comparison label stays
        ``endpoint.provider``.
        """
        if task.endpoint is None or not task.model:
            return task.model or ""
        provider, _, model = task.model.partition("/")
        assert provider == task.endpoint.provider  # re-checked in provider_config
        return f"{OPENCODE_PROVIDER_ID}/{model}"

    def endpoint_env(self, task: AgentTask) -> dict[str, str]:
        """The generated provider config as OpenCode's inline config JSON.

        ``OPENCODE_CONFIG_CONTENT`` is merged after every file-based config
        source, so the endpoint's ``baseURL`` cannot be overridden by a
        workspace ``opencode.json`` or ``.opencode`` directory config. The
        payload carries the key's variable name, never its value.
        """
        if task.endpoint is None:
            return {}
        return {OPENCODE_CONFIG_CONTENT: json.dumps(self.provider_config(task))}

    def provider_config(self, task: AgentTask) -> dict[str, Any]:
        """The generated OpenCode provider entry for a custom endpoint.

        Registered under :data:`OPENCODE_PROVIDER_ID` so OpenCode uses the
        ``@ai-sdk/openai-compatible`` package configured here instead of a
        built-in loader for a well-known slug. The API key is OpenCode's
        ``{env:<name>}`` substitution, so the config carries the key's
        variable name, never its value.
        """
        endpoint = task.endpoint
        assert endpoint is not None
        # Config validation rejects these; re-checked here so a programmatic
        # AgentTask cannot point --model at another provider and bypass the
        # gateway.
        provider, separator, model = (task.model or "").partition("/")
        if not separator or provider != endpoint.provider:
            raise ConfigurationError(
                f"opencode model {task.model!r} must be "
                f"'{endpoint.provider}/<model>' to run through the "
                f"'{endpoint.provider}' endpoint"
            )
        if task.reasoning_effort and task.reasoning_effort not in _OPENCODE_EFFORTS:
            # A variant the generated config does not declare is dropped by
            # OpenCode's request builder: the requested effort would silently
            # have no effect, so the trial must not start.
            raise ConfigurationError(
                f"opencode supports reasoning efforts {', '.join(_OPENCODE_EFFORTS)} "
                f"for a custom endpoint, not {task.reasoning_effort!r}"
            )
        entry: dict[str, Any] = {
            "npm": "@ai-sdk/openai-compatible",
            "name": endpoint.provider,
            "options": {
                "baseURL": endpoint.base_url,
                "apiKey": f"{{env:{endpoint.api_key_env}}}",
            },
            # Models must be declared for a custom provider; the command's
            # model ID is rewritten to "<internal>/<model>", so the entry key
            # drops the requested provider prefix. A config-defined model
            # defaults to reasoning=false and no variants, which would leave
            # --variant with nothing to apply, so both are declared.
            "models": {
                model: {
                    "reasoning": True,
                    "variants": {
                        effort: {"reasoningEffort": effort} for effort in _OPENCODE_EFFORTS
                    },
                }
            },
        }
        return {"$schema": "opencode.ai/config.json", "provider": {OPENCODE_PROVIDER_ID: entry}}

    def validate_options(self, options: dict[str, Any]) -> None:
        unknown = set(options) - _OPTIONS
        if unknown:
            raise ValueError(
                f"unknown opencode options {sorted(unknown)}; known: {sorted(_OPTIONS)}"
            )

    # -- output ---------------------------------------------------------------

    def parse_output(self, stdout: str) -> ParsedOutput:
        steps: list[dict[str, Any]] = []
        errors: list[str] = []
        session_id = None
        for event in json_lines(stdout):
            session_id = session_id or event.get("sessionID")
            kind = event.get("type")
            part = event.get("part") if isinstance(event.get("part"), dict) else {}
            assert isinstance(part, dict)
            if kind == "step_finish":
                steps.append({"tokens": part.get("tokens"), "cost": part.get("cost")})
            elif kind == "error":
                errors.append(_error_message(event.get("error")))
        raw = {"session_id": session_id, "steps": steps, "errors": errors}
        return ParsedOutput(raw=raw, errors=errors)

    def parse_usage(self, raw: dict[str, Any]) -> tuple[UsageMetrics, Metric[str]]:
        steps = [s for s in raw.get("steps") or [] if isinstance(s, dict)]
        sums: dict[str, int] = {}
        seen: set[str] = set()
        cost = 0.0
        has_cost = False
        for step in steps:
            tokens = step.get("tokens") if isinstance(step.get("tokens"), dict) else {}
            assert isinstance(tokens, dict)
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            assert isinstance(cache, dict)
            for key, value in (
                ("input", tokens.get("input")),
                ("output", tokens.get("output")),
                ("reasoning", tokens.get("reasoning")),
                ("cache_read", cache.get("read")),
                ("cache_write", cache.get("write")),
            ):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    sums[key] = sums.get(key, 0) + int(value)
                    seen.add(key)
            if isinstance(step.get("cost"), (int, float)):
                cost += float(step["cost"])
                has_cost = True

        def get(key: str) -> int | None:
            return sums[key] if key in seen else None

        fresh, out = get("input"), get("output")
        read, written = get("cache_read"), get("cache_write") or 0
        # OpenCode reports cache reads/writes outside "input"; normalise like Claude Code.
        total_in = None if fresh is None else fresh + written + (read or 0)
        tokens_metrics = TokenUsage(
            accounting_scheme=None,  # depends on the provider behind the model
            input_tokens=token_metric(total_in, "input + cache writes + cache reads"),
            cached_input_tokens=token_metric(read),
            output_tokens=token_metric(out),
            reasoning_tokens=token_metric(get("reasoning")),
            total_tokens=token_metric(None if total_in is None or out is None else total_in + out),
        )
        usage = UsageMetrics(
            tokens=tokens_metrics,
            cost=(
                Metric[float].of(cost, MetricSource.AGENT_REPORTED, unit="USD")
                if has_cost
                else Metric[float].unavailable("not reported by the agent", unit="USD")
            ),
            turns=(
                Metric[int].of(len(steps), MetricSource.AGENT_REPORTED, note="model steps")
                if steps
                else Metric[int].unavailable("not reported by the agent")
            ),
        )
        return usage, Metric[str].unavailable("opencode does not report the model used")


def _error_message(error: Any) -> str:
    if not isinstance(error, dict):
        return str(error)
    data = error.get("data")
    if isinstance(data, dict) and data.get("message"):
        status = data.get("statusCode")
        return f"{data['message']} (HTTP {status})" if status else str(data["message"])
    return str(error.get("name") or error)
