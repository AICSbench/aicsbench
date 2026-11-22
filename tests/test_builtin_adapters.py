"""Built-in adapters: command lines, auth defaults and parsing of real output samples."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ai_benchmark.agents import AgentTask, default_registry
from ai_benchmark.agents.agy import AgyAdapter
from ai_benchmark.agents.claude_code import ClaudeCodeAdapter
from ai_benchmark.agents.cli import CliAgentAdapter
from ai_benchmark.agents.codex import CodexAdapter
from ai_benchmark.agents.commandcode import CommandCodeAdapter
from ai_benchmark.agents.opencode import OpenCodeAdapter
from ai_benchmark.config.experiment import EndpointConfig
from ai_benchmark.environment.base import CommandResult
from ai_benchmark.errors import ConfigurationError
from ai_benchmark.models import AuthMode

SAMPLES = Path(__file__).parent / "fixtures" / "agents"


class StubRunner:
    """Writes canned stdout to the log file, like the Docker runner streams it."""

    def __init__(self, stdout: str, exit_code: int = 0, stderr: str = "") -> None:
        self.stdout, self.exit_code, self.stderr = stdout, exit_code, stderr
        self.calls: list[dict[str, Any]] = []

    def run(self, command: Any, *, timeout_seconds: int, **kw: Any) -> CommandResult:
        self.calls.append({"command": list(command), "timeout": timeout_seconds, **kw})
        if kw.get("stdout_path"):
            kw["stdout_path"].write_text(self.stdout)
        if kw.get("stderr_path"):
            kw["stderr_path"].write_text(self.stderr)
        return CommandResult(
            command=list(command),
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            duration_seconds=1.0,
        )


def task(tmp_path: Path, **kw: Any) -> AgentTask:
    defaults: dict[str, Any] = dict(
        trial_id="t1",
        prompt="Build the API",
        workdir="/workspace",
        log_dir=tmp_path,
        timeout_seconds=600,
        auth_mode=AuthMode.SUBSCRIPTION,
    )
    return AgentTask(**{**defaults, **kw})


def run(adapter: CliAgentAdapter, tmp_path: Path, sample: str, exit_code: int = 0) -> Any:
    runner = StubRunner((SAMPLES / sample).read_text(), exit_code)
    return adapter.run(task(tmp_path, model="m"), runner), runner


def test_builtins_are_registered() -> None:
    assert {"agy", "claude", "codex", "commandcode", "opencode"} <= set(default_registry.names())


@pytest.mark.parametrize(
    "cls", [ClaudeCodeAdapter, CodexAdapter, CommandCodeAdapter, OpenCodeAdapter]
)
def test_image_layer_pins_version_and_needs_node(cls: type[CliAgentAdapter]) -> None:
    layer = cls().image_layer()
    assert layer.cli_version == cls.cli_version
    assert "node" in layer.requires
    assert all(f"@{cls.cli_version}" in c for c in layer.install_commands)
    assert "latest" not in " ".join(layer.install_commands)


def test_agy_image_layer_pins_official_linux_builds() -> None:
    layer = AgyAdapter().image_layer()
    assert layer.cli_version == "1.5.14"
    assert layer.requires == []
    install = " ".join(layer.install_commands)
    assert "1.5.14-5302690847260672/linux-x64/cli_linux_x64.tar.gz" in install
    assert "1.5.14-5302690847260672/linux-arm/cli_linux_arm64.tar.gz" in install
    # Checksums published in the official linux_amd64/linux_arm64 manifests.
    assert (
        "83311d3f354567612dc16c1ae2ea9b503d6f6833aa4e138d39aa229da3f3bb99"
        "b0e4d7cca0bce4796b0060d6a51d8113eb7449ed5dc0e7900bf217107ea5bff9"
    ) in install
    assert (
        "c9b29bba475f63a1a80161ae93ba72b221c7a586d9851e4d0061546704bab66"
        "d6b2760b25d716d191724fc7564f3246456ee151e28aa37af48dbcd72bdc6c463"
    ) in install
    assert "sha512sum -c -" in install
    assert '"modelProvider":"gemini"' in install
    assert '"mcp(*)"' in install
    assert "latest" not in install


def test_agy_is_api_key_only() -> None:
    adapter = AgyAdapter()
    auth = adapter.default_auth(AuthMode.API_KEY)
    assert auth.env == ["GEMINI_API_KEY"]
    assert auth.missing({}) == ["environment variable GEMINI_API_KEY is not set"]
    assert auth.missing({"GEMINI_API_KEY": "x"}) == []
    assert adapter.capabilities().supported_auth_modes == (AuthMode.API_KEY,)
    with pytest.raises(ConfigurationError, match="subscription auth"):
        adapter.default_auth(AuthMode.SUBSCRIPTION)


def test_agy_rejects_custom_endpoints_and_unknown_options(tmp_path: Path) -> None:
    endpoint = EndpointConfig(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
    )
    with pytest.raises(ConfigurationError, match="custom endpoints"):
        AgyAdapter().build_command(task(tmp_path, endpoint=endpoint))
    with pytest.raises(ValueError, match="unknown agy options"):
        AgyAdapter().validate_options({"api_key_env": "GEMINI_API_KEY"})


@pytest.mark.parametrize(
    ("cls", "subscription", "api_key"),
    [
        (ClaudeCodeAdapter, ["CLAUDE_CODE_OAUTH_TOKEN"], ["ANTHROPIC_API_KEY"]),
        (CodexAdapter, [], ["CODEX_API_KEY"]),
        (OpenCodeAdapter, [], ["ANTHROPIC_API_KEY"]),
    ],
)
def test_default_auth(
    cls: type[CliAgentAdapter], subscription: list[str], api_key: list[str]
) -> None:
    sub = cls().default_auth(AuthMode.SUBSCRIPTION)
    assert sub.env == subscription
    # Never mix: a subscription run must not receive an API key variable.
    assert not set(sub.env) & set(api_key)
    assert cls().default_auth(AuthMode.API_KEY).env == api_key


def test_file_credentials_land_in_agent_home() -> None:
    codex = CodexAdapter().default_auth(AuthMode.SUBSCRIPTION).files[0]
    assert codex.target == "/home/agent/.codex/auth.json"
    oc = OpenCodeAdapter().default_auth(AuthMode.SUBSCRIPTION).files[0]
    assert oc.target == "/home/agent/.local/share/opencode/auth.json"


def test_opencode_api_key_variable_is_configurable() -> None:
    adapter = OpenCodeAdapter({"api_key_env": "OPENAI_API_KEY"})
    assert adapter.default_auth(AuthMode.API_KEY).env == ["OPENAI_API_KEY"]
    with pytest.raises(ValueError, match="unknown opencode options"):
        adapter.validate_options({"nope": 1})


# -- command lines ------------------------------------------------------------------


def test_claude_command(tmp_path: Path) -> None:
    cmd = ClaudeCodeAdapter().build_command(task(tmp_path, model="sonnet", reasoning_effort="high"))
    assert cmd[:2] == ["claude", "--print"]
    assert cmd[2:4] == ["--output-format", "json"]
    assert "bypassPermissions" in cmd and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert cmd[-2:] == ["--", "Build the API"]


def test_codex_command(tmp_path: Path) -> None:
    cmd = CodexAdapter().build_command(task(tmp_path, model="gpt-6", reasoning_effort="high"))
    assert cmd[:3] == ["codex", "exec", "--json"]
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert cmd[cmd.index("--cd") + 1] == "/workspace"
    assert cmd[cmd.index("--model") + 1] == "gpt-6"
    assert 'model_reasoning_effort="high"' in cmd
    assert cmd[-2:] == ["--", "Build the API"]


def test_opencode_command(tmp_path: Path) -> None:
    cmd = OpenCodeAdapter().build_command(
        task(tmp_path, model="anthropic/claude-sonnet-5-5", reasoning_effort="max")
    )
    assert cmd[:5] == ["opencode", "run", "--format", "json", "--auto"]
    assert cmd[cmd.index("--model") + 1] == "anthropic/claude-sonnet-5-5"
    assert cmd[cmd.index("--variant") + 1] == "max"
    assert cmd[-2:] == ["--", "Build the API"]


def test_agy_command(tmp_path: Path) -> None:
    cmd = AgyAdapter().build_command(
        task(tmp_path, model="gemini-3.8-flash-medium", reasoning_effort="high")
    )
    assert cmd[:2] == ["agy", "--print"]
    assert cmd[2] == "Build the API"
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--dangerously-skip-permissions" in cmd
    assert "--disable-slash-commands" in cmd
    assert cmd[cmd.index("--mode") + 1] == "accept-edits"
    assert cmd[cmd.index("--agent") + 1] == "default"
    assert cmd[cmd.index("--model") + 1] == "gemini-3.8-flash-medium"
    assert cmd[cmd.index("--effort") + 1] == "high"


def test_agy_rejects_unknown_effort(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="reasoning efforts"):
        AgyAdapter().build_command(task(tmp_path, reasoning_effort="ultra"))


def test_defaults_omit_model_and_effort(tmp_path: Path) -> None:
    for adapter in (AgyAdapter(), ClaudeCodeAdapter(), CodexAdapter(), OpenCodeAdapter()):
        cmd = adapter.build_command(task(tmp_path))
        assert "--model" not in cmd and "--effort" not in cmd and "--variant" not in cmd


# -- parsing real output samples ------------------------------------------------------


def test_claude_success(tmp_path: Path) -> None:
    result, runner = run(ClaudeCodeAdapter(), tmp_path, "claude_success.json")
    assert result.succeeded and result.error is None and not result.rate_limit_events
    tokens = result.usage.tokens
    assert tokens.accounting_scheme == "anthropic"
    assert tokens.input_tokens.value == 2 + 11828 + 26625
    assert tokens.cached_input_tokens.value == 26625
    assert tokens.output_tokens.value == 49
    assert tokens.reasoning_tokens.value == 5
    assert tokens.total_tokens.value == 2 + 11828 + 26625 + 49
    assert result.usage.cost.value == pytest.approx(0.100937)
    assert result.usage.turns.value == 7
    assert result.reported_model.value == "claude-opus-5-5"
    assert "claude-haiku-5-5" in (result.reported_model.note or "")
    assert result.raw["session_id"].startswith("a78742b3")
    # The prompt is not repeated in the stored command line.
    assert "Build the API" not in result.command and "<prompt>" in result.command
    assert runner.calls[0]["workdir"] == "/workspace"
    assert runner.calls[0]["timeout"] == 600


def test_claude_usage_limit(tmp_path: Path) -> None:
    result, _ = run(ClaudeCodeAdapter(), tmp_path, "claude_usage_limit.json", exit_code=1)
    assert result.error and result.error.startswith("Usage limit reached")
    assert result.stopped_by_rate_limit
    assert result.rate_limit_events[0].message.startswith("Usage limit reached")


def test_codex_success(tmp_path: Path) -> None:
    result, _ = run(CodexAdapter(), tmp_path, "codex_success.jsonl")
    assert result.succeeded and not result.rate_limit_events
    tokens = result.usage.tokens
    assert tokens.accounting_scheme == "openai"
    assert tokens.input_tokens.value == 24763
    assert tokens.cached_input_tokens.value == 24448
    assert tokens.output_tokens.value == 122
    assert tokens.reasoning_tokens.value == 64
    assert tokens.total_tokens.value == 24763 + 122
    assert not result.usage.cost.is_available
    assert not result.usage.turns.is_available
    assert not result.reported_model.is_available
    assert result.raw["final_message"] == "Implemented the API."


def test_codex_usage_limit(tmp_path: Path) -> None:
    result, _ = run(CodexAdapter(), tmp_path, "codex_usage_limit.jsonl", exit_code=1)
    assert result.stopped_by_rate_limit
    assert len(result.rate_limit_events) == 1  # same message from error + turn.failed
    assert "hit your usage limit" in result.rate_limit_events[0].message
    assert not result.usage.tokens.input_tokens.is_available


def test_opencode_success(tmp_path: Path) -> None:
    result, _ = run(OpenCodeAdapter(), tmp_path, "opencode_success.jsonl")
    assert result.succeeded and not result.rate_limit_events
    tokens = result.usage.tokens
    assert tokens.input_tokens.value == (1200 + 800 + 100) + (100 + 2000)
    assert tokens.cached_input_tokens.value == 2800
    assert tokens.output_tokens.value == 350
    assert tokens.reasoning_tokens.value == 20
    assert result.usage.cost.value == pytest.approx(0.015)
    assert result.usage.turns.value == 2
    assert result.raw["session_id"] == "ses_1"


def test_opencode_rate_limit(tmp_path: Path) -> None:
    result, _ = run(OpenCodeAdapter(), tmp_path, "opencode_rate_limit.jsonl", exit_code=1)
    assert result.stopped_by_rate_limit
    assert "HTTP 429" in result.rate_limit_events[0].message
    assert not result.usage.tokens.input_tokens.is_available
    assert not result.usage.turns.is_available


def test_commandcode_headless_result(tmp_path: Path) -> None:
    runner = StubRunner(
        '{"type":"event","event":{"type":"run_end"}}\n'
        '{"type":"result","subtype":"success","usage":{"inputTokens":1200,'
        '"outputTokens":50,"cacheReadTokens":800,"cacheWriteTokens":100}}\n'
    )
    result = CommandCodeAdapter().run(
        task(tmp_path, model="example", reasoning_effort="low"), runner
    )
    assert result.succeeded
    assert "--yolo" in runner.calls[0]["command"]
    assert runner.calls[0]["command"][-6:] == [
        "--model",
        "example",
        "--effort",
        "low",
        "--print",
        "Build the API",
    ]
    assert result.command[-1] == "<prompt>"
    assert result.usage.tokens.input_tokens.value == 1200
    assert result.usage.tokens.cached_input_tokens.value == 800
    assert result.usage.tokens.total_tokens.value == 1250
    assert not result.reported_model.is_available


@pytest.mark.parametrize(("inputs", "expected"), [(0, None), (1200, 1200)])
def test_commandcode_error_retains_only_nonzero_usage(
    tmp_path: Path, inputs: int, expected: int | None
) -> None:
    runner = StubRunner(
        '{"type":"result","subtype":"error","error":"HTTP 429: quota exceeded",'
        f'"usage":{{"inputTokens":{inputs},"outputTokens":0,"cacheReadTokens":0}}}}\n',
        exit_code=3,
    )
    result = CommandCodeAdapter().run(task(tmp_path), runner)
    assert result.stopped_by_rate_limit
    assert result.usage.tokens.total_tokens.value == expected


def test_agy_success(tmp_path: Path) -> None:
    result, runner = run(AgyAdapter(), tmp_path, "agy_success.jsonl")
    assert result.succeeded and result.error is None and not result.rate_limit_events
    tokens = result.usage.tokens
    assert tokens.accounting_scheme == "gemini"
    assert tokens.input_tokens.value == 10418
    assert tokens.cached_input_tokens.value == 8113
    assert tokens.output_tokens.value == 589
    assert tokens.reasoning_tokens.value == 551
    assert tokens.total_tokens.value == 11007
    assert not result.usage.cost.is_available
    assert result.usage.turns.value == 1
    assert "user turns" in (result.usage.turns.note or "")
    assert not result.reported_model.is_available
    assert result.raw["status"] == "SUCCESS"
    assert result.raw["conversation_id"].startswith("11111111")
    assert result.raw["init"]["agent"] == "default"
    assert result.raw["steps"] == {
        "total": 3,
        "tools": ["write_to_file"],
        "errors": 0,
    }
    # The prompt is not repeated in the stored command line.
    assert "Build the API" not in result.command and "<prompt>" in result.command
    assert runner.calls[0]["workdir"] == "/workspace"
    assert runner.calls[0]["timeout"] == 600


def test_agy_terminal_error(tmp_path: Path) -> None:
    result, _ = run(AgyAdapter(), tmp_path, "agy_error.jsonl", exit_code=1)
    assert not result.succeeded
    assert result.error and result.error.startswith("invalid model selection")
    assert "does-not-exist" in result.error
    assert not result.rate_limit_events
    for metric in (
        result.usage.tokens.input_tokens,
        result.usage.tokens.cached_input_tokens,
        result.usage.tokens.output_tokens,
        result.usage.tokens.reasoning_tokens,
        result.usage.tokens.total_tokens,
    ):
        assert not metric.is_available
    assert not result.usage.cost.is_available
    assert result.raw["status"] == "ERROR"


def test_agy_absent_usage_is_unavailable_not_zero(tmp_path: Path) -> None:
    result, _ = run(AgyAdapter(), tmp_path, "agy_absent_usage.jsonl")
    assert result.succeeded
    for metric in (
        result.usage.tokens.input_tokens,
        result.usage.tokens.cached_input_tokens,
        result.usage.tokens.output_tokens,
        result.usage.tokens.reasoning_tokens,
        result.usage.tokens.total_tokens,
    ):
        assert not metric.is_available
        assert metric.value is None
    assert not result.usage.cost.is_available
    assert result.usage.turns.value == 1


def test_agy_rate_limit_from_structured_error(tmp_path: Path) -> None:
    runner = StubRunner(
        (SAMPLES / "agy_rate_limit.jsonl").read_text(),
        exit_code=3,
        stderr=(SAMPLES / "agy_rate_limit.stderr.txt").read_text(),
    )
    result = AgyAdapter().run(task(tmp_path), runner)
    assert result.stopped_by_rate_limit
    assert "Quota exceeded" in result.rate_limit_events[0].message
    assert not result.usage.tokens.input_tokens.is_available
    assert result.raw["agy_error"]["error_code"] == 429


def test_agy_missing_result_is_an_error(tmp_path: Path) -> None:
    runner = StubRunner('{"event":"init","conversation_id":"x","init":{"cwd":"/workspace"}}\n')
    result = AgyAdapter().run(task(tmp_path), runner)
    assert result.error and "no terminal result event" in result.error
    assert not result.usage.tokens.input_tokens.is_available


def test_agy_provider_is_google() -> None:
    from ai_benchmark.models.results import AgentRecord

    assert AgentRecord.default_provider("agy", "gemini-3.8-flash-medium") == "google"


def test_garbage_output_is_unavailable_not_zero(tmp_path: Path) -> None:
    for adapter in (AgyAdapter(), ClaudeCodeAdapter(), CodexAdapter(), OpenCodeAdapter()):
        runner = StubRunner("segmentation fault\n", exit_code=139)
        result = adapter.run(task(tmp_path), runner)
        assert not result.succeeded
        assert not result.usage.tokens.output_tokens.is_available
        assert not result.usage.cost.is_available
        assert not result.rate_limit_events


def test_limit_text_in_stderr_is_detected(tmp_path: Path) -> None:
    runner = StubRunner("", exit_code=1, stderr="Error: 429 Too Many Requests\n")
    result = CodexAdapter().run(task(tmp_path), runner)
    assert result.stopped_by_rate_limit


@pytest.mark.parametrize(
    ("output", "version"),
    [
        ("2.1.323 (Claude Code)\n", "2.1.323"),
        ("codex-cli 0.167.2\n", "0.167.2"),
        ("1.19.5\n", "1.19.5"),
        ("1.5.14\n", "1.5.14"),
    ],
)
def test_version_parsing(output: str, version: str) -> None:
    runner = StubRunner(output)
    assert ClaudeCodeAdapter().get_version(runner) == version


def test_availability_flags_version_drift() -> None:
    avail = ClaudeCodeAdapter().check_availability(StubRunner("2.0.0 (Claude Code)\n"))
    assert avail.installed and avail.version == "2.0.0"
    assert avail.message and "expected 2.1.323" in avail.message
    missing = CodexAdapter().check_availability(StubRunner("", exit_code=127))
    assert not missing.installed
