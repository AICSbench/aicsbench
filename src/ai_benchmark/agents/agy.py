"""Google Antigravity CLI (``agy``) adapter.

Checked against the official docs (headless mode, authentication, CLI reference)
and the pinned CLI itself (``agy --version``, ``agy --help``, ``agy models``,
and real headless error runs) on 2026-11-22.

* Headless runs use ``--print <prompt> --output-format stream-json``. stdout is
  NDJSON: one ``init`` event, any number of ``step_update`` events, and one
  terminal ``result`` event (the same envelope as ``--output-format json``).
* The result reports ``status`` (``SUCCESS``, ``ERROR``, ...), ``error`` on
  failure, ``num_turns`` and ``usage`` with ``input_tokens``,
  ``output_tokens``, ``thinking_tokens``, ``cache_read_tokens`` and
  ``total_tokens``. In the pinned CLI's samples ``total_tokens`` is
  ``input_tokens + output_tokens``: cache reads are included in input, and
  thinking tokens are reported separately without being added to the total.
* API-key auth: ``modelProvider`` must be ``gemini`` in
  ``~/.gemini/antigravity-cli/settings.json`` and ``GEMINI_API_KEY`` must be in
  the environment. The agent image writes that settings file; the key is
  injected per trial by name and never baked into the image or results.
* The CLI's own ``--print-timeout`` is left at its default (no limit); the
  trial runner enforces ``AgentTask.timeout_seconds`` and reports timeouts.
* An unknown ``--model`` exits non-zero with status ``ERROR``. The adapter
  passes the requested slug through and never falls back to another model.

The standard benchmark setup pins the built-in ``default`` agent, disables
slash-command/skill expansion, denies MCP tools, and installs no plugins or
custom agents. ``--dangerously-skip-permissions`` auto-approves tool calls
because the trial container is the isolation boundary: the CLI runs as a
non-root user with no Docker socket, a per-trial workspace and network, and
credentials injected per trial.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, ClassVar

from ai_benchmark.agents.base import (
    AgentCapabilities,
    AgentRunResult,
    AgentTask,
)
from ai_benchmark.agents.cli import (
    AGENT_HOME,
    RATE_LIMIT_PATTERNS,
    CliAgentAdapter,
    ParsedOutput,
    int_or_none,
    json_lines,
    token_metric,
)
from ai_benchmark.agents.registry import register_agent
from ai_benchmark.config.auth import AgentAuthConfig
from ai_benchmark.config.experiment import EndpointConfig
from ai_benchmark.environment.base import AgentImageLayer, CommandRunner
from ai_benchmark.errors import ConfigurationError
from ai_benchmark.models.enums import AuthMode, MetricSource
from ai_benchmark.models.metrics import Metric, TokenUsage, UsageMetrics
from ai_benchmark.models.results import RateLimitEvent

# Pinned from the official release manifests on 2026-11-22:
# https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_amd64.json
# https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_arm64.json
_AGY_RELEASE = "1.5.14-5302690847260672"
_AGY_AMD64_URL = (
    "https://storage.googleapis.com/antigravity-public/antigravity-cli/"
    f"{_AGY_RELEASE}/linux-x64/cli_linux_x64.tar.gz"
)
_AGY_AMD64_SHA512 = (
    "83311d3f354567612dc16c1ae2ea9b503d6f6833aa4e138d39aa229da3f3bb99"
    "b0e4d7cca0bce4796b0060d6a51d8113eb7449ed5dc0e7900bf217107ea5bff9"
)
_AGY_ARM64_URL = (
    "https://storage.googleapis.com/antigravity-public/antigravity-cli/"
    f"{_AGY_RELEASE}/linux-arm/cli_linux_arm64.tar.gz"
)
_AGY_ARM64_SHA512 = (
    "c9b29bba475f63a1a80161ae93ba72b221c7a586d9851e4d0061546704bab66"
    "d6b2760b25d716d191724fc7564f3246456ee151e28aa37af48dbcd72bdc6c463"
)

_AGY_SETTINGS = {
    "modelProvider": "gemini",
    # The standard benchmark setup has no MCP servers (tooling mode standard);
    # denying them also blocks MCP tools an installed/workspace plugin could add.
    "permissions": {"deny": ["mcp(*)"]},
}
_AGY_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_AGY_RATE_LIMIT_STATUSES = frozenset(
    {"RESOURCE_EXHAUSTED", "RATE_LIMIT_EXCEEDED", "TOO_MANY_REQUESTS"}
)
_LIMIT_RE = re.compile("|".join(RATE_LIMIT_PATTERNS), re.IGNORECASE)


def _install_command() -> str:
    """The Dockerfile RUN line that installs the pinned official CLI.

    The release tarball is verified against the SHA-512 published in the
    official manifest before it is extracted, so a changed artifact fails the
    image build instead of silently changing the harness. The binary is
    installed root-owned in ``/usr/local/bin``, outside the agent user's home,
    so a background self-update cannot replace the pinned build.
    """
    settings = shlex.quote(json.dumps(_AGY_SETTINGS, separators=(",", ":")))
    settings_dir = f"{AGENT_HOME}/.gemini/antigravity-cli"
    settings_path = f"{settings_dir}/settings.json"
    amd64 = (
        f"x86_64|amd64) url={shlex.quote(_AGY_AMD64_URL)}; sha={shlex.quote(_AGY_AMD64_SHA512)};;"
    )
    arm64 = (
        f"aarch64|arm64) url={shlex.quote(_AGY_ARM64_URL)}; sha={shlex.quote(_AGY_ARM64_SHA512)};;"
    )
    return " ".join(
        [
            "set -eu;",
            'case "$(uname -m)" in',
            amd64,
            arm64,
            '*) echo "agy: unsupported architecture $(uname -m); '
            'supported: x86_64, aarch64" >&2; exit 1;;',
            "esac;",
            'curl -fsSL --retry 3 --connect-timeout 20 --max-time 600 -o /tmp/agy.tar.gz "$url";',
            'echo "$sha  /tmp/agy.tar.gz" | sha512sum -c -;',
            "tar -xzf /tmp/agy.tar.gz -C /usr/local/bin antigravity;",
            "mv /usr/local/bin/antigravity /usr/local/bin/agy;",
            "chmod 0755 /usr/local/bin/agy;",
            "rm -f /tmp/agy.tar.gz;",
            # Start from an empty CLI home: no plugins, custom agents or stale
            # settings from the base image. Only the provider setting is kept.
            f"rm -rf {AGENT_HOME}/.gemini;",
            f"mkdir -p {settings_dir};",
            f"printf '%s\\n' {settings} > {settings_path};",
            f'chown -R "$(stat -c %u {AGENT_HOME}):$(stat -c %g {AGENT_HOME})" '
            f"{AGENT_HOME}/.gemini;",
            f"chmod 0600 {settings_path};",
            "agy --version",
        ]
    )


@register_agent
class AgyAdapter(CliAgentAdapter):
    name = "agy"
    display_name = "Google Antigravity"
    executable = "agy"
    cli_version: ClassVar[str] = "1.5.14"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            structured_output=True,
            reports_token_usage=True,
            reports_turns=True,
            reports_rate_limits=True,
            supports_reasoning_effort=True,
            supports_mcp=False,
            supported_auth_modes=(AuthMode.API_KEY,),
            token_accounting_scheme="gemini",
        )

    def image_layer(self) -> AgentImageLayer:
        return AgentImageLayer(
            agent=self.name,
            cli_version=self.cli_version,
            install_commands=[_install_command()],
        )

    def default_auth(self, mode: AuthMode) -> AgentAuthConfig:
        if mode is AuthMode.SUBSCRIPTION:
            raise ConfigurationError(
                "agy supports only API-key auth in this adapter; "
                "run it with --auth api_key and GEMINI_API_KEY"
            )
        return AgentAuthConfig(mode=mode, env=["GEMINI_API_KEY"])

    def endpoint_auth(self, endpoint: EndpointConfig) -> AgentAuthConfig:
        raise ConfigurationError("agy does not support custom endpoints")

    def validate_options(self, options: dict[str, Any]) -> None:
        if options:
            raise ValueError(f"unknown agy options {sorted(options)}; agy takes none")

    def build_command(self, task: AgentTask) -> list[str]:
        if task.endpoint is not None:
            raise ConfigurationError("agy does not support custom endpoints")
        cmd = [
            self.executable,
            "--print",
            task.prompt,
            "--output-format",
            "stream-json",
            # The container is the execution boundary; approve tools and pin an
            # edit mode that does not route commands through the host sandbox.
            "--dangerously-skip-permissions",
            "--disable-slash-commands",
            "--mode",
            "accept-edits",
            "--agent",
            "default",
        ]
        if task.model:
            cmd += ["--model", task.model]
        if task.reasoning_effort:
            if task.reasoning_effort not in _AGY_EFFORTS:
                raise ConfigurationError(
                    f"agy supports reasoning efforts {', '.join(_AGY_EFFORTS)}, "
                    f"not {task.reasoning_effort!r}"
                )
            cmd += ["--effort", task.reasoning_effort]
        return cmd

    # -- output ---------------------------------------------------------------

    def parse_output(self, stdout: str) -> ParsedOutput:
        init: dict[str, Any] = {}
        steps = 0
        tools: list[str] = []
        error_steps = 0
        result: dict[str, Any] | None = None
        for event in json_lines(stdout):
            kind = event.get("event")
            if kind == "init":
                payload = event.get("init")
                if isinstance(payload, dict):
                    init = payload
            elif kind == "step_update":
                payload = event.get("step_update")
                if not isinstance(payload, dict):
                    continue
                steps += 1
                if payload.get("step_type") == "tool" and isinstance(payload.get("tool_name"), str):
                    tools.append(payload["tool_name"])
                if payload.get("step_type") == "error_message":
                    error_steps += 1
            elif kind == "result":
                payload = event.get("result")
                if isinstance(payload, dict):
                    result = payload
            elif kind is None and isinstance(event.get("status"), str):
                # Tolerate a bare JSON envelope if a future build emits one.
                result = event

        raw: dict[str, Any] = {
            "init": init,
            "steps": {"total": steps, "tools": tools, "errors": error_steps},
        }
        if result is None:
            raw["status"] = None
            return ParsedOutput(raw=raw, errors=["no terminal result event in Antigravity output"])

        raw.update(result)
        errors: list[str] = []
        error = result.get("error")
        status = result.get("status")
        if isinstance(error, str) and error.strip():
            errors.append(error.strip())
        elif status != "SUCCESS":
            errors.append(f"Antigravity ended with status {status or 'unknown'}")
        return ParsedOutput(raw=raw, errors=errors)

    def parse_usage(self, raw: dict[str, Any]) -> tuple[UsageMetrics, Metric[str]]:
        usage = raw.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        inputs = int_or_none(usage.get("input_tokens"))
        outputs = int_or_none(usage.get("output_tokens"))
        thinking = int_or_none(usage.get("thinking_tokens"))
        cached = int_or_none(usage.get("cache_read_tokens"))
        reported_total = int_or_none(usage.get("total_tokens"))

        # The CLI emits a zeroed usage object when a run fails before inference
        # (unknown model, invalid key). Keep that apart from real zero usage.
        counts = (inputs, outputs, thinking, cached, reported_total)
        if raw.get("status") != "SUCCESS" and not any(
            count is not None and count > 0 for count in counts
        ):
            inputs = outputs = thinking = cached = reported_total = None

        total = reported_total
        if total is None and inputs is not None and outputs is not None:
            total = inputs + outputs

        tokens = TokenUsage(
            accounting_scheme="gemini",
            input_tokens=token_metric(inputs, "all input tokens, including cache reads"),
            cached_input_tokens=token_metric(cached, "included in input tokens"),
            output_tokens=token_metric(outputs),
            reasoning_tokens=token_metric(thinking, "reported separately; not added to the total"),
            total_tokens=token_metric(
                total,
                "reported total"
                if reported_total is not None
                else "input + output (total not reported)",
            ),
        )
        turns = int_or_none(raw.get("num_turns"))
        return (
            UsageMetrics(
                tokens=tokens,
                cost=Metric[float].unavailable("Antigravity does not report cost", unit="USD"),
                turns=(
                    Metric[int].of(
                        turns,
                        MetricSource.AGENT_REPORTED,
                        note="user turns in the conversation, not agentic turns",
                    )
                    if turns is not None
                    else Metric[int].unavailable("not reported by the agent")
                ),
            ),
            Metric[str].unavailable("Antigravity does not report which model answered"),
        )

    def run(self, task: AgentTask, runner: CommandRunner) -> AgentRunResult:
        result = super().run(task, runner)
        stderr = _read_text(result.stderr_path)
        structured = _structured_error(stderr)
        if structured is not None:
            result.raw["agy_error"] = structured
            message = _structured_message(structured)
            if message and (
                result.error is None or result.error.startswith("no terminal result event")
            ):
                result.error = message[:1000]
            if _is_rate_limit(structured) and not result.stopped_by_rate_limit:
                result.rate_limit_events.append(
                    RateLimitEvent(
                        message=message[:500] or "Antigravity rate limit",
                        fatal=True,
                    )
                )
        elif result.error is None and not result.timed_out and result.exit_code not in (0, None):
            last = _last_error_line(stderr)
            if last:
                result.error = last[:1000]
        if result.exit_code not in (0, None) and result.error is None:
            result.error = f"agy exited with code {result.exit_code}"
        return result


def _read_text(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _structured_error(stderr: str) -> dict[str, Any] | None:
    """The last ``AGY_ERROR: {...}`` line the CLI prints on failure."""
    for line in reversed(stderr.splitlines()):
        line = line.strip()
        if not line.startswith("AGY_ERROR:"):
            continue
        try:
            value = json.loads(line.removeprefix("AGY_ERROR:").strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _structured_message(error: dict[str, Any]) -> str:
    for key in ("short_error", "message", "error", "status"):
        value = error.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def _is_rate_limit(error: dict[str, Any]) -> bool:
    status = str(error.get("status") or "").upper()
    if status in _AGY_RATE_LIMIT_STATUSES or str(error.get("error_code")) == "429":
        return True
    return bool(_LIMIT_RE.search(_structured_message(error)))


def _last_error_line(stderr: str) -> str:
    for line in reversed(stderr.splitlines()):
        text = line.strip()
        if text.lower().startswith("error:"):
            return " ".join(text.split())
    return ""
