"""Custom model endpoints (docs/phase-5/tasks/09-custom-endpoints.md)."""

from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from ai_benchmark.agents import AgentRegistry, AgentTask
from ai_benchmark.agents.claude_code import ClaudeCodeAdapter
from ai_benchmark.agents.codex import CodexAdapter
from ai_benchmark.agents.opencode import OPENCODE_CONFIG_PATH, OpenCodeAdapter
from ai_benchmark.cli.app import app
from ai_benchmark.comparison import compare_trials
from ai_benchmark.config import AgentSelection, EndpointConfig, ExperimentConfig, SpecCatalog
from ai_benchmark.config.settings import PlatformSettings
from ai_benchmark.environment import CommandResult
from ai_benchmark.errors import AgentExecutionError, ConfigurationError
from ai_benchmark.models import (
    AgentRecord,
    AuthMode,
    ComparisonDimension,
    Metric,
    MetricSource,
    TrialMetrics,
    TrialStatus,
    UsageMetrics,
)
from ai_benchmark.orchestrator import BenchmarkOrchestrator
from ai_benchmark.orchestrator.policy import (
    CUSTOM_ENDPOINT_COST_NOTE,
    REPORTED_COST_KEY,
    apply_auth_policy,
)
from ai_benchmark.storage import JsonResultStore
from ai_benchmark.submission.validate import validate_bundle

from .conftest import REPO_ROOT
from .fakes import FakeAdapter, FakeEnvironment
from .test_builtin_adapters import task as adapter_task
from .test_storage import manifest, trial

ENDPOINT = EndpointConfig(
    provider="openrouter",
    base_url="https://openrouter.ai/api/v1",
    api_key_env="OPENROUTER_API_KEY",
)
KEY_ENV = "OPENROUTER_API_KEY"
MARKER = "sk-or-marker-value-never-recorded"


def endpoint_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": KEY_ENV,
    }
    return {**payload, **overrides}


def selection(agent: str, model: str, **overrides: Any) -> dict[str, Any]:
    return {"agent": agent, "model": model, "endpoint": endpoint_payload(), **overrides}


def experiment_config(agents: list[dict[str, Any]], **overrides: Any) -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        {
            "task": "task-management-api",
            "agents": agents,
            "stacks": ["fastapi"],
            "trials": 1,
            "seed": 3,
            "auth_mode": "api_key",
            **overrides,
        }
    )


# -- endpoint configuration ---------------------------------------------------


def test_endpoint_host_strips_scheme_port_and_path() -> None:
    endpoint = EndpointConfig(
        provider="litellm",
        base_url="https://gw.example.com:8443/v1/x",
        api_key_env="LITELLM_KEY",
    )
    assert endpoint.host == "gw.example.com"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:4000",
        "http://litellm:4000/v1",
        "http://127.0.0.1:4000",
        "http://10.0.0.5",
        "http://192.168.1.5:4000",
        "http://[::1]:4000",
    ],
)
def test_plain_http_is_allowed_for_local_and_private_hosts(base_url: str) -> None:
    endpoint = EndpointConfig(provider="litellm", base_url=base_url, api_key_env="LITELLM_KEY")
    assert endpoint.host


def test_endpoint_rejects_user_info() -> None:
    with pytest.raises(ValueError, match="user info"):
        EndpointConfig(
            provider="openrouter",
            base_url=f"https://user:{MARKER}@openrouter.ai/api/v1",
            api_key_env=KEY_ENV,
        )


def test_endpoint_rejects_query_string() -> None:
    with pytest.raises(ValueError, match="query string"):
        EndpointConfig(
            provider="openrouter",
            base_url=f"https://openrouter.ai/api/v1?key={MARKER}",
            api_key_env=KEY_ENV,
        )


def test_endpoint_rejects_http_to_a_public_host() -> None:
    with pytest.raises(ValueError, match="plain http"):
        EndpointConfig(
            provider="openrouter",
            base_url="http://gateway.example.com/v1",
            api_key_env=KEY_ENV,
        )


@pytest.mark.parametrize("base_url", ["https:///v1", "https://:8443/v1", "http:///v1"])
def test_endpoint_requires_a_host_name(base_url: str) -> None:
    with pytest.raises(ValueError, match="host name"):
        EndpointConfig(provider="openrouter", base_url=base_url, api_key_env=KEY_ENV)


@pytest.mark.parametrize(
    "base_url",
    ["https://openrouter.ai:99999/api/v1", "https://openrouter.ai:notaport/api/v1"],
)
def test_endpoint_rejects_an_invalid_port(base_url: str) -> None:
    with pytest.raises(ValueError, match="invalid port"):
        EndpointConfig(provider="openrouter", base_url=base_url, api_key_env=KEY_ENV)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://16909060",  # the integer form of 1.2.3.4: a public IP, dotless
        "http://0x01020304",  # the hex form of the same public IP
        "http://[2001:4860:4860::8888]/v1",  # a public IPv6 literal
        # WHATWG URL parsing reads a leading zero as octal, so these connect
        # to public addresses (1.223.244.112 and 64.0.0.0), not to the private
        # or non-existent decimal readings.
        "http://0167772160/v1",
        "http://010000000000/v1",
    ],
)
def test_endpoint_rejects_plain_http_to_a_public_ip_literal(base_url: str) -> None:
    with pytest.raises(ValueError, match="plain http"):
        EndpointConfig(provider="openrouter", base_url=base_url, api_key_env=KEY_ENV)


def test_endpoint_allows_https_to_an_octal_numeric_host() -> None:
    # Only plain http is restricted; https to the same host stays allowed.
    assert EndpointConfig(
        provider="openrouter", base_url="https://0167772160/v1", api_key_env=KEY_ENV
    )


def test_endpoint_allows_plain_http_to_a_private_hex_ip_literal() -> None:
    assert EndpointConfig(provider="openrouter", base_url="http://0x7f000001", api_key_env=KEY_ENV)


def test_endpoint_rejects_an_unbracketed_ipv6_url() -> None:
    # urlsplit cannot tell host from port here, so it is rejected as a port.
    with pytest.raises(ValueError, match="invalid port"):
        EndpointConfig(
            provider="openrouter", base_url="http://2001:4860:4860::8888", api_key_env=KEY_ENV
        )


def test_endpoint_rejects_an_empty_api_key_env_name() -> None:
    with pytest.raises(ValueError, match="environment variable name"):
        EndpointConfig(
            provider="openrouter", base_url="https://openrouter.ai/api/v1", api_key_env=""
        )


def test_endpoint_requires_api_key_auth_on_the_selection() -> None:
    with pytest.raises(ValueError, match="endpoint requires auth mode 'api_key'"):
        AgentSelection.model_validate(
            {
                "agent": "claude",
                "auth": {"mode": "subscription", "env": ["CLAUDE_CODE_OAUTH_TOKEN"]},
                "endpoint": endpoint_payload(),
            }
        )


def test_experiment_rejects_endpoint_with_subscription_auth_mode() -> None:
    with pytest.raises(ValueError, match="requires auth_mode 'api_key'"):
        experiment_config(
            [selection("claude", "m")],
            auth_mode="subscription",
        )


def test_experiment_accepts_endpoint_with_api_key_auth_mode() -> None:
    config = experiment_config([selection("claude", "openai/gpt-6-luna")])
    assert config.agents[0].endpoint is not None


# -- selection keys ------------------------------------------------------------


def test_selection_key_adds_the_endpoint_identity_only_with_an_endpoint() -> None:
    assert AgentSelection(agent="codex", model="m").key == "codex:m"
    assert AgentSelection(agent="codex").key == "codex"
    gatewayed_key = AgentSelection.model_validate(selection("codex", "m")).key
    assert gatewayed_key == "codex:m@openrouter:https://openrouter.ai/api/v1"


def test_selection_key_separates_endpoints_differing_in_port_or_path(
    env_settings: Any,
) -> None:
    baseline = AgentSelection.model_validate(selection("codex", "m")).key
    other_port = AgentSelection.model_validate(
        {
            **selection("codex", "m"),
            "endpoint": endpoint_payload(base_url="https://openrouter.ai:4001/api/v1"),
        }
    ).key
    other_path = AgentSelection.model_validate(
        {
            **selection("codex", "m"),
            "endpoint": endpoint_payload(base_url="https://openrouter.ai/api/v2"),
        }
    ).key
    # Provider and host are identical in every pair, so dropping either the
    # port or the path from the identity would make one of these fail.
    assert len({baseline, other_port, other_path}) == 3


def test_endpoint_selections_differing_only_in_endpoint_are_distinct(
    env_settings: Any,
) -> None:
    other = endpoint_payload(
        provider="litellm", base_url="https://gw.example.com/v1", api_key_env="LITELLM_KEY"
    )
    config = experiment_config(
        [selection("claude", "m"), {**selection("claude", "m"), "endpoint": other}]
    )
    plan = build(env_settings, FakeEnvironment()).plan(config)
    assert len({t.trial_id for t in plan.trials}) == len(plan.trials)


def test_identical_endpoint_selections_are_still_rejected() -> None:
    with pytest.raises(ValueError, match="must be unique"):
        experiment_config([selection("claude", "m"), selection("claude", "m")])


# -- opencode endpoint model form ----------------------------------------------


def test_opencode_endpoint_selection_requires_a_model() -> None:
    with pytest.raises(ValueError, match="needs a model in the form 'openrouter/<model>'"):
        AgentSelection.model_validate({"agent": "opencode", "endpoint": endpoint_payload()})


def test_opencode_endpoint_model_must_name_the_endpoint_provider() -> None:
    with pytest.raises(ValueError, match="'openai'"):
        AgentSelection.model_validate(
            {"agent": "opencode", "model": "openai/gpt-6-luna", "endpoint": endpoint_payload()}
        )


def test_non_opencode_endpoint_selections_keep_their_own_model_form() -> None:
    # Only OpenCode's model form is 'provider/model': the other harnesses may
    # run their default or a bare model ID through the gateway.
    assert AgentSelection.model_validate({"agent": "claude", "endpoint": endpoint_payload()})
    assert AgentSelection.model_validate(selection("codex", "gpt-6-luna"))


def test_opencode_provider_config_rejects_a_foreign_provider_model(tmp_path: Path) -> None:
    task = adapter_task(
        Path("/tmp"),
        model="openai/gpt-6-luna",
        endpoint=ENDPOINT,
        auth_mode=AuthMode.API_KEY,
    )
    with pytest.raises(ConfigurationError, match="openai/gpt-6-luna"):
        OpenCodeAdapter().provider_config(task)


# -- adapter mappings ---------------------------------------------------------


def test_claude_endpoint_mapping() -> None:
    adapter = ClaudeCodeAdapter()
    assert adapter.endpoint_env(ENDPOINT) == {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api/v1"}
    auth = adapter.endpoint_auth(ENDPOINT)
    assert auth.env == []
    # The gateway token is renamed: the container's ANTHROPIC_AUTH_TOKEN is read
    # from the host variable, by name only.
    assert auth.env_from == {"ANTHROPIC_AUTH_TOKEN": KEY_ENV}


def test_codex_and_opencode_endpoint_auth_pass_the_variable_by_name() -> None:
    for adapter in (CodexAdapter(), OpenCodeAdapter()):
        auth = adapter.endpoint_auth(ENDPOINT)
        assert auth.env == [KEY_ENV]
        assert auth.env_from == {}


# -- auth resolution with an explicit provider key -----------------------------


def test_resolve_auth_drops_the_adapter_api_key_from_explicit_auth(
    env_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KEY_ENV, MARKER)
    orchestrator = build(env_settings, FakeEnvironment())
    orchestrator.registry.register(ClaudeCodeAdapter, replace=True)
    agent_selection = AgentSelection.model_validate(
        {
            **selection("claude", "gpt-6-luna"),
            "auth": {"mode": "api_key", "env": ["ANTHROPIC_API_KEY"]},
        }
    )
    auth = orchestrator.resolve_auth(agent_selection, AuthMode.API_KEY)
    # Claude Code prefers ANTHROPIC_API_KEY over ANTHROPIC_AUTH_TOKEN, so the
    # explicit provider key must not reach the container next to the gateway
    # token.
    assert auth.env == []
    assert auth.env_from == {"ANTHROPIC_AUTH_TOKEN": KEY_ENV}


def test_resolve_auth_keeps_the_endpoint_variable_from_explicit_auth(
    env_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KEY_ENV, MARKER)
    orchestrator = build(env_settings, FakeEnvironment())
    orchestrator.registry.register(CodexAdapter, replace=True)
    agent_selection = AgentSelection.model_validate(
        {
            **selection("codex", "gpt-6-luna"),
            "auth": {"mode": "api_key", "env": ["CODEX_API_KEY", KEY_ENV]},
        }
    )
    auth = orchestrator.resolve_auth(agent_selection, AuthMode.API_KEY)
    # Codex's own default variable is dropped; the endpoint's variable stays.
    assert auth.env == [KEY_ENV]


def test_resolve_auth_drops_shadowed_env_from_entries(
    env_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KEY_ENV, MARKER)
    monkeypatch.setenv("PROVIDER_KEY_HOST", MARKER + "-host")
    orchestrator = build(env_settings, FakeEnvironment())
    orchestrator.registry.register(ClaudeCodeAdapter, replace=True)
    agent_selection = AgentSelection.model_validate(
        {
            **selection("claude", "gpt-6-luna"),
            "auth": {"mode": "api_key", "env_from": {"ANTHROPIC_API_KEY": "PROVIDER_KEY_HOST"}},
        }
    )
    auth = orchestrator.resolve_auth(agent_selection, AuthMode.API_KEY)
    # An explicit env_from renaming the provider's API-key variable shadows the
    # gateway token exactly like an explicit env entry, so it is dropped too;
    # the gateway token's own renaming survives unchanged.
    assert auth.env == []
    assert auth.env_from == {"ANTHROPIC_AUTH_TOKEN": KEY_ENV}


def test_resolve_auth_keeps_explicit_auth_without_an_endpoint(
    env_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KEY_ENV, MARKER)
    orchestrator = build(env_settings, FakeEnvironment())
    agent_selection = AgentSelection.model_validate(
        {
            "agent": "claude",
            "model": "m",
            "auth": {"mode": "api_key", "env": ["ANTHROPIC_API_KEY"]},
        }
    )
    auth = orchestrator.resolve_auth(agent_selection, AuthMode.API_KEY)
    assert auth.env == ["ANTHROPIC_API_KEY"]
    assert auth.env_from == {}


def test_codex_endpoint_command() -> None:
    cmd = CodexAdapter().build_command(
        adapter_task(
            Path("/tmp"), model="gpt-6-luna", endpoint=ENDPOINT, auth_mode=AuthMode.API_KEY
        )
    )
    assert 'model_provider="bench"' in cmd
    assert 'model_providers.bench.name="bench"' in cmd
    assert 'model_providers.bench.base_url="https://openrouter.ai/api/v1"' in cmd
    assert f'model_providers.bench.env_key="{KEY_ENV}"' in cmd
    assert not any("wire_api" in part for part in cmd)


def test_codex_endpoint_command_maps_wire_api() -> None:
    endpoint = EndpointConfig(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env=KEY_ENV,
        wire_api="responses",
    )
    cmd = CodexAdapter().build_command(
        adapter_task(
            Path("/tmp"), model="gpt-6-luna", endpoint=endpoint, auth_mode=AuthMode.API_KEY
        )
    )
    assert 'model_providers.bench.wire_api="responses"' in cmd


def test_opencode_provider_config() -> None:
    adapter = OpenCodeAdapter()
    task = adapter_task(
        Path("/tmp"),
        model="openrouter/openai/gpt-6-luna",
        endpoint=ENDPOINT,
        auth_mode=AuthMode.API_KEY,
    )
    config = adapter.provider_config(task)
    entry = config["provider"]["openrouter"]
    assert entry["options"]["baseURL"] == "https://openrouter.ai/api/v1"
    # The key is OpenCode's {env:VAR} substitution: a name, never a value.
    assert entry["options"]["apiKey"] == f"{{env:{KEY_ENV}}}"
    assert entry["models"] == {"openai/gpt-6-luna": {}}


class RecordingRunner:
    """A CommandRunner that records every call and writes the log files.

    ``sh -c`` write commands are executed in miniature: the ``printf %s
    <payload> > <target>`` part is interpreted by writing the payload to
    ``scratch_dir`` mirroring the container path, so tests can assert on the
    generated file instead of the command string.
    """

    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        timed_out: bool = False,
        scratch_dir: Path | None = None,
    ) -> None:
        self.stdout, self.stderr = stdout, stderr
        self.exit_code, self.timed_out = exit_code, timed_out
        self.scratch_dir = scratch_dir
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        command: Any,
        *,
        timeout_seconds: int,
        env: Any = None,
        workdir: Any = None,
        stdout_path: Any = None,
        stderr_path: Any = None,
    ) -> CommandResult:
        self.calls.append({"command": list(command), "env": dict(env or {})})
        if (
            self.scratch_dir is not None
            and self.exit_code == 0
            and not self.timed_out
            and command[:2] == ["sh", "-c"]
        ):
            self._write_script_payload(command[2])
        for path, text in ((stdout_path, self.stdout), (stderr_path, self.stderr)):
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        return CommandResult(
            command=list(command),
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            duration_seconds=0.0,
            timed_out=self.timed_out,
        )

    def _write_script_payload(self, script: str) -> None:
        """Interpret ``mkdir -p <dir> && printf %s <payload> > <target>``."""
        assert self.scratch_dir is not None
        tokens = shlex.split(script)
        marker = tokens.index("printf")
        payload, target = tokens[marker + 2], tokens[tokens.index(">", marker) + 1]
        assert payload and target.startswith("/")
        path = self.scratch_dir / target.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")


@pytest.mark.parametrize(
    ("adapter_cls", "expected"),
    [
        (ClaudeCodeAdapter, ["ANTHROPIC_BASE_URL"]),
        (
            CodexAdapter,
            [
                "model_providers.bench.name",
                "model_providers.bench.base_url",
                "model_providers.bench.env_key",
            ],
        ),
        (OpenCodeAdapter, ["baseURL", f"{{env:{KEY_ENV}}}"]),
    ],
)
def test_endpoint_key_value_never_leaks(
    adapter_cls: type, expected: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KEY_ENV, MARKER)
    runner = RecordingRunner(stdout='{"type": "result"}')
    task = AgentTask(
        trial_id="t1",
        prompt="Build the API",
        workdir="/workspace",
        log_dir=tmp_path,
        timeout_seconds=600,
        auth_mode=AuthMode.API_KEY,
        model="openrouter/openai/gpt-6-luna",
        endpoint=ENDPOINT,
    )
    result = adapter_cls().run(task, runner)

    surfaces = [json.dumps(call) for call in runner.calls]
    surfaces += [p.read_text(errors="replace") for p in tmp_path.rglob("*") if p.is_file()]
    surfaces.append(result.model_dump_json())
    joined = "\n".join(surfaces)
    # Sanity: the mapping really happened, by variable name only.
    assert "https://openrouter.ai/api/v1" in joined
    for fragment in expected:
        assert fragment in joined
    assert MARKER not in joined


def test_opencode_run_writes_the_provider_config(tmp_path: Path) -> None:
    scratch = tmp_path / "container"
    runner = RecordingRunner(stdout='{"type": "step_finish"}', scratch_dir=scratch)
    task = AgentTask(
        trial_id="t1",
        prompt="Build the API",
        workdir="/workspace",
        log_dir=tmp_path,
        timeout_seconds=600,
        auth_mode=AuthMode.API_KEY,
        model="openrouter/openai/gpt-6-luna",
        endpoint=ENDPOINT,
    )
    OpenCodeAdapter().run(task, runner)
    # The write command targets OpenCode's documented global config.
    write = next(call for call in runner.calls if call["command"][:2] == ["sh", "-c"])
    assert f"> {shlex.quote(OPENCODE_CONFIG_PATH)}" in write["command"][2]
    agent = next(call for call in runner.calls if call["command"][0] == "opencode")
    assert "--model openrouter/openai/gpt-6-luna" in " ".join(agent["command"])

    # The generated file exists, parses and carries the variable name only.
    config_path = scratch / OPENCODE_CONFIG_PATH.lstrip("/")
    assert config_path.is_file()
    text = config_path.read_text(encoding="utf-8")
    config = json.loads(text)
    assert config["$schema"] == "opencode.ai/config.json"
    entry = config["provider"]["openrouter"]
    assert entry["options"]["baseURL"] == ENDPOINT.base_url
    assert entry["options"]["apiKey"] == f"{{env:{KEY_ENV}}}"
    assert entry["models"] == {"openai/gpt-6-luna": {}}
    assert MARKER not in text


def test_opencode_run_fails_when_the_config_write_fails(tmp_path: Path) -> None:
    runner = RecordingRunner(
        stdout='{"type": "step_finish"}',
        exit_code=1,
        stderr="cannot create opencode.json: Permission denied\n",
    )
    task = AgentTask(
        trial_id="t1",
        prompt="Build the API",
        workdir="/workspace",
        log_dir=tmp_path,
        timeout_seconds=600,
        auth_mode=AuthMode.API_KEY,
        model="openrouter/openai/gpt-6-luna",
        endpoint=ENDPOINT,
    )
    with pytest.raises(AgentExecutionError, match="exit code 1.*Permission denied"):
        OpenCodeAdapter().run(task, runner)
    # OpenCode must not start with missing or stale config.
    assert not any(call["command"][0] == "opencode" for call in runner.calls)


def test_opencode_run_fails_when_the_config_write_times_out(tmp_path: Path) -> None:
    runner = RecordingRunner(stdout='{"type": "step_finish"}', timed_out=True)
    task = AgentTask(
        trial_id="t1",
        prompt="Build the API",
        workdir="/workspace",
        log_dir=tmp_path,
        timeout_seconds=600,
        auth_mode=AuthMode.API_KEY,
        model="openrouter/openai/gpt-6-luna",
        endpoint=ENDPOINT,
    )
    with pytest.raises(AgentExecutionError, match="timed out"):
        OpenCodeAdapter().run(task, runner)
    assert not any(call["command"][0] == "opencode" for call in runner.calls)


# -- orchestrator wiring ------------------------------------------------------


class CostlyHarnessAdapter(FakeAdapter):
    """A fake harness that reports an agent-computed cost, like the real CLIs can.

    Every run's ``AgentTask`` is captured on the subclass, so tests can assert
    what the orchestrator actually forwarded to the adapter.
    """

    captured: ClassVar[list[Any]] = []

    def run(self, task: Any, runner: Any) -> Any:
        type(self).captured.append(task)
        result = super().run(task, runner)
        usage = result.usage.model_copy()
        usage.cost = Metric[float].of(0.42, MetricSource.AGENT_REPORTED, unit="USD")
        result.usage = usage
        return result


class FakeClaude(CostlyHarnessAdapter):
    name = "claude"
    captured: ClassVar[list[Any]] = []


class FakeCodex(CostlyHarnessAdapter):
    name = "codex"
    captured: ClassVar[list[Any]] = []


class FakeOpencode(CostlyHarnessAdapter):
    name = "opencode"
    captured: ClassVar[list[Any]] = []


@pytest.fixture
def env_settings(tmp_path: Path) -> PlatformSettings:
    tasks = tmp_path / "tasks"
    shutil.copytree(REPO_ROOT / "benchmarks" / "tasks", tasks)
    (tasks / "task-management-api" / "acceptance").mkdir(exist_ok=True)
    (tasks / "task-management-api" / "acceptance" / "Dockerfile").write_text("FROM scratch\n")
    return PlatformSettings(
        tasks_dir=tasks,
        stacks_dir=REPO_ROOT / "benchmarks" / "stacks",
        results_dir=tmp_path / "results",
        workspaces_dir=tmp_path / "workspaces",
    )


def build(settings: PlatformSettings, env: FakeEnvironment) -> BenchmarkOrchestrator:
    registry = AgentRegistry()
    registry._entry_points_loaded = True
    for cls in (FakeClaude, FakeCodex, FakeOpencode):
        registry.register(cls)
    return BenchmarkOrchestrator(
        settings=settings,
        catalog=SpecCatalog(settings),
        registry=registry,
        environment=env,
        store=JsonResultStore(settings.results_dir),
    )


def test_one_model_on_three_harnesses_through_one_endpoint(
    env_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KEY_ENV, MARKER)
    for cls in (FakeClaude, FakeCodex, FakeOpencode):
        cls.captured.clear()
    env = FakeEnvironment()
    orchestrator = build(env_settings, env)
    config = experiment_config(
        [
            selection("claude", "openai/gpt-6-luna"),
            selection("codex", "openai/gpt-6-luna"),
            selection("opencode", "openrouter/openai/gpt-6-luna"),
        ]
    )
    summary = orchestrator.run(config)
    assert summary.by_status == {"passed": 3}

    # The configured endpoint reaches the adapter inside AgentTask: the
    # harness, not the orchestrator, is what maps it.
    for cls in (FakeClaude, FakeCodex, FakeOpencode):
        [task] = cls.captured
        assert task.endpoint == ENDPOINT
        assert task.auth_mode is AuthMode.API_KEY

    trials = list(orchestrator.store.iter_trials())
    assert {t.agent.name for t in trials} == {"claude", "codex", "opencode"}
    for item in trials:
        assert item.agent.provider == "openrouter"
        assert item.agent.endpoint_host == "openrouter.ai"
        assert item.agent.model_key == "openai/gpt-6-luna"
        assert item.metrics.usage.cost.is_available is False
        assert item.metrics.usage.cost.note == CUSTOM_ENDPOINT_COST_NOTE
        assert item.metrics.provider_specific[REPORTED_COST_KEY] == 0.42
    # The gateway key travels by variable name only.
    auths = [auth for step, auth in env.calls if step == "auth"]
    assert auths and all(auth.env == [KEY_ENV] and auth.env_from == {} for auth in auths)

    result = CliRunner().invoke(
        app,
        [
            "--results-dir",
            str(env_settings.results_dir),
            "compare",
            "--run",
            summary.run_id,
            "--by",
            "harness",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "codex" in result.output


def test_direct_runs_record_the_harness_default_provider(
    env_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .fakes import FAKE_API_KEY_ENV

    monkeypatch.setenv(FAKE_API_KEY_ENV, MARKER)
    orchestrator = build(env_settings, FakeEnvironment())
    config = experiment_config(
        [
            {"agent": "claude", "model": "claude-opus"},
            {"agent": "codex", "model": "gpt-6-luna"},
            {"agent": "opencode", "model": "openai/gpt-6-luna"},
        ]
    )
    summary = orchestrator.run(config)
    providers = {item.agent.name: item.agent.provider for item in orchestrator.store.iter_trials()}
    assert providers == {"claude": "anthropic", "codex": "openai", "opencode": "openai"}
    assert all(item.agent.endpoint_host is None for item in orchestrator.store.iter_trials())
    assert summary.by_status == {"passed": 3}


# -- provider comparability ---------------------------------------------------


def gatewayed(
    trial_id: str,
    *,
    agent: str = "codex",
    model: str | None = "openai/gpt-6-luna",
    provider: str = "openrouter",
    endpoint_host: str | None = "openrouter.ai",
) -> Any:
    item = trial("r1", trial_id, agent, TrialStatus.PASSED)
    item.agent.resolved_model = model
    item.agent.provider = provider
    item.agent.endpoint_host = endpoint_host
    return item


def test_harness_comparison_accepts_one_provider_one_model_key() -> None:
    trials = [
        gatewayed("a", agent="claude"),
        gatewayed("b", agent="codex"),
        gatewayed("c", agent="opencode", model="openrouter/openai/gpt-6-luna"),
    ]
    rows = compare_trials(trials, ComparisonDimension.HARNESS)
    assert {row.value for row in rows} == {"claude", "codex", "opencode"}


def test_harness_comparison_refuses_mixed_providers() -> None:
    # A direct Codex run against OpenAI is never ranked against a gateway run.
    direct = gatewayed("a", provider="openai", endpoint_host=None, model="gpt-6-luna")
    gateway = gatewayed("b")
    for dimension in (ComparisonDimension.HARNESS, ComparisonDimension.AGENT):
        with pytest.raises(ValueError, match="harness comparison mixes providers"):
            compare_trials([direct, gateway], dimension)


def test_agent_model_comparison_refuses_mixed_providers() -> None:
    # Same harness and model label, one direct and one through a gateway: the
    # rows must not be combined by the harness+model dimensions either.
    direct = gatewayed("a", provider="openai", endpoint_host=None, model="gpt-6-luna")
    gateway = gatewayed("b", model="gpt-6-luna")
    for dimension in (ComparisonDimension.AGENT_MODEL, ComparisonDimension.HARNESS_MODEL):
        with pytest.raises(ValueError, match="harness comparison mixes providers"):
            compare_trials([direct, gateway], dimension)


def test_explicit_provider_dimension_groups_legacy_trials_like_by_provider() -> None:
    # Unknown-provider trials are pre-endpoint-era direct runs (custom
    # endpoints did not exist before provider tracking), so with PROVIDER as
    # an explicit dimension they group into one row, exactly like --by
    # provider does; plain --by harness still refuses to combine them.
    legacy_a = gatewayed("a", provider=None, endpoint_host=None)
    legacy_b = gatewayed("b", provider=None, endpoint_host=None)
    with pytest.raises(ValueError, match="recorded provider"):
        compare_trials([legacy_a, legacy_b], ComparisonDimension.HARNESS)
    rows = compare_trials(
        [legacy_a, legacy_b], [ComparisonDimension.HARNESS, ComparisonDimension.PROVIDER]
    )
    assert {row.value for row in rows} == {"codex + unknown"}


def test_harness_and_provider_dimensions_group_per_provider() -> None:
    # Grouping by provider explicitly asks for one row per provider.
    direct = gatewayed("a", provider="openai", endpoint_host=None, model="gpt-6-luna")
    gateway = gatewayed("b", model="gpt-6-luna")
    rows = compare_trials(
        [direct, gateway], [ComparisonDimension.HARNESS, ComparisonDimension.PROVIDER]
    )
    assert {row.value for row in rows} == {"codex + openai", "codex + openrouter"}


def test_harness_and_provider_dimensions_allow_an_unknown_provider() -> None:
    # 'unknown' is shown as its own provider, like --by provider does.
    legacy = trial("r1", "a", "codex", TrialStatus.PASSED)
    modern = gatewayed("b", provider="openai", endpoint_host=None, model=None)
    rows = compare_trials(
        [legacy, modern], [ComparisonDimension.HARNESS, ComparisonDimension.PROVIDER]
    )
    assert {row.value for row in rows} == {"codex + unknown", "codex + openai"}


def test_harness_and_provider_dimensions_still_check_comparability() -> None:
    one = gatewayed("a")
    other = gatewayed("b")
    other.max_repairs = 1
    with pytest.raises(ValueError, match="repair budget"):
        compare_trials([one, other], [ComparisonDimension.HARNESS, ComparisonDimension.PROVIDER])


def test_provider_dimension_needs_one_harness_model_and_stack() -> None:
    one = gatewayed("a", agent="codex")
    other = gatewayed("b", agent="opencode", model="openrouter/openai/gpt-6-luna")
    with pytest.raises(ValueError, match="mixes harnesses, models or stacks"):
        compare_trials([one, other], ComparisonDimension.PROVIDER)


def test_provider_dimension_groups_by_provider() -> None:
    direct = gatewayed("a", provider="openai", endpoint_host=None)
    gateway = gatewayed("b")
    rows = compare_trials([direct, gateway], ComparisonDimension.PROVIDER)
    assert {row.value for row in rows} == {"openai", "openrouter"}


def test_stored_trial_without_provider_loads_as_unknown(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    payload = trial("r1", "t1", "codex", TrialStatus.PASSED).model_dump(mode="json")
    payload["agent"].pop("provider")
    path = tmp_path / "r1" / "trials" / "t1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    [item] = list(store.iter_trials())
    assert item.agent.provider is None
    assert item.dimension_value(ComparisonDimension.PROVIDER) == "unknown"

    modern = trial("r1", "t2", "codex", TrialStatus.PASSED)
    modern.agent.provider = "openai"
    with pytest.raises(ValueError, match="needs a recorded provider"):
        compare_trials([item, modern], ComparisonDimension.HARNESS)
    # Legacy trials still appear in model and stack comparisons.
    assert [row.value for row in compare_trials([item, modern], ComparisonDimension.STACK)] == [
        "fastapi"
    ]


def test_harness_comparison_refuses_every_unknown_provider() -> None:
    legacy = [trial("r1", name, "codex", TrialStatus.PASSED) for name in ("a", "b", "c")]
    with pytest.raises(ValueError, match="needs a recorded provider"):
        compare_trials(legacy, ComparisonDimension.HARNESS)
    assert [row.value for row in compare_trials(legacy, ComparisonDimension.STACK)] == ["fastapi"]


def test_compare_cli_refuses_unknown_provider_mix_but_allows_stack(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    legacy = trial("r1", "t1", "codex", TrialStatus.PASSED)
    modern = trial("r1", "t2", "codex", TrialStatus.PASSED)
    modern.agent.provider = "openai"
    store.save_trial(legacy)
    store.save_trial(modern)
    runner = CliRunner()
    args = ["--results-dir", str(tmp_path), "compare", "--run", "r1"]

    refused = runner.invoke(app, [*args, "--by", "harness"])
    assert refused.exit_code == 1
    assert "needs a recorded provider" in refused.output

    allowed = runner.invoke(app, [*args, "--by", "stack"])
    assert allowed.exit_code == 0, allowed.output


# -- cost policy --------------------------------------------------------------


def test_endpoint_cost_is_unavailable_with_note() -> None:
    metrics = TrialMetrics()
    usage = UsageMetrics()
    usage.cost = Metric[float].of(0.42, MetricSource.AGENT_REPORTED, unit="USD")
    metrics.usage = usage

    applied = apply_auth_policy(metrics, AuthMode.API_KEY, custom_endpoint=True)
    assert applied.usage.cost.is_available is False
    assert applied.usage.cost.note == CUSTOM_ENDPOINT_COST_NOTE
    assert applied.provider_specific[REPORTED_COST_KEY] == 0.42

    untouched = apply_auth_policy(metrics, AuthMode.API_KEY, custom_endpoint=False)
    assert untouched.usage.cost.value == 0.42


# -- submission validation ----------------------------------------------------


@pytest.fixture
def catalog(settings: Any) -> Any:
    from ai_benchmark.config import SpecCatalog

    return SpecCatalog(settings)


def with_endpoint_host(tmp_path: Path, catalog: Any, host: str) -> Any:
    from .test_submission import copy_bundle, read_json, refresh_hashes, write_json

    bundle = copy_bundle(tmp_path)
    data = read_json(bundle, "trials/trial-001.json")
    data["agent"]["endpoint_host"] = host
    write_json(bundle, "trials/trial-001.json", data)
    refresh_hashes(bundle, ["trials/trial-001.json"])
    return bundle


@pytest.mark.parametrize("host", ["openrouter.ai", "api.anthropic.com", "api.openai.com", "custom"])
def test_validator_accepts_allowlisted_or_custom_endpoint_host(
    catalog: Any, tmp_path: Path, host: str
) -> None:
    assert validate_bundle(with_endpoint_host(tmp_path, catalog, host), catalog) == []


def test_validator_rejects_a_private_gateway_host(catalog: Any, tmp_path: Path) -> None:
    bundle = with_endpoint_host(tmp_path, catalog, "llm.internal.example")
    problems = validate_bundle(bundle, catalog)
    assert [problem.code for problem in problems] == ["schema"]
    assert "endpoint_host" in problems[0].message
    assert "trials/trial-001.json" in problems[0].path
    # The submitted value itself stays out of the diagnostic: a gateway URL
    # can carry credentials and the CLI prints problems without redaction.
    assert "llm.internal.example" not in problems[0].message


# -- provider defaults on the record -----------------------------------------


def test_default_provider_of_direct_runs() -> None:
    assert AgentRecord.default_provider("claude", None) == "anthropic"
    assert AgentRecord.default_provider("codex", None) == "openai"
    assert AgentRecord.default_provider("opencode", "openai/gpt-6-luna") == "openai"
    assert AgentRecord.default_provider("opencode", None) is None
