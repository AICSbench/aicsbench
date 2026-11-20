"""Custom model endpoints (docs/phase-5/tasks/09-custom-endpoints.md)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ai_benchmark.agents import AgentRegistry, AgentTask
from ai_benchmark.agents.claude_code import ClaudeCodeAdapter
from ai_benchmark.agents.codex import CodexAdapter
from ai_benchmark.agents.opencode import OpenCodeAdapter
from ai_benchmark.cli.app import app
from ai_benchmark.comparison import compare_trials
from ai_benchmark.config import AgentSelection, EndpointConfig, ExperimentConfig, SpecCatalog
from ai_benchmark.config.settings import PlatformSettings
from ai_benchmark.environment import CommandResult
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


def test_codex_endpoint_command() -> None:
    cmd = CodexAdapter().build_command(
        adapter_task(
            Path("/tmp"), model="gpt-6-luna", endpoint=ENDPOINT, auth_mode=AuthMode.API_KEY
        )
    )
    assert 'model_provider="bench"' in cmd
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
    """A CommandRunner that records every call and writes the log files."""

    def __init__(self, stdout: str = "", stderr: str = "") -> None:
        self.stdout, self.stderr = stdout, stderr
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
        for path, text in ((stdout_path, self.stdout), (stderr_path, self.stderr)):
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        return CommandResult(
            command=list(command),
            exit_code=0,
            stdout=self.stdout,
            stderr=self.stderr,
            duration_seconds=0.0,
        )


@pytest.mark.parametrize(
    ("adapter_cls", "expected"),
    [
        (ClaudeCodeAdapter, ["ANTHROPIC_BASE_URL"]),
        (CodexAdapter, ["model_providers.bench.base_url", "model_providers.bench.env_key"]),
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
        model="openai/gpt-6-luna",
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
    runner = RecordingRunner(stdout='{"type": "step_finish"}')
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
    write = next(call for call in runner.calls if call["command"][:2] == ["sh", "-c"])
    agent = next(call for call in runner.calls if call["command"][0] == "opencode")
    assert "--model openrouter/openai/gpt-6-luna" in " ".join(agent["command"])
    config = json.loads(
        write["command"][2].split("printf %s ")[1].split(" > /home/agent/")[0].strip("'")
    )
    assert config["provider"]["openrouter"]["options"]["baseURL"] == ENDPOINT.base_url


# -- orchestrator wiring ------------------------------------------------------


class CostlyHarnessAdapter(FakeAdapter):
    """A fake harness that reports an agent-computed cost, like the real CLIs can."""

    def run(self, task: Any, runner: Any) -> Any:
        result = super().run(task, runner)
        usage = result.usage.model_copy()
        usage.cost = Metric[float].of(0.42, MetricSource.AGENT_REPORTED, unit="USD")
        result.usage = usage
        return result


class FakeClaude(CostlyHarnessAdapter):
    name = "claude"


class FakeCodex(CostlyHarnessAdapter):
    name = "codex"


class FakeOpencode(CostlyHarnessAdapter):
    name = "opencode"


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
    model: str = "openai/gpt-6-luna",
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


# -- provider defaults on the record -----------------------------------------


def test_default_provider_of_direct_runs() -> None:
    assert AgentRecord.default_provider("claude", None) == "anthropic"
    assert AgentRecord.default_provider("codex", None) == "openai"
    assert AgentRecord.default_provider("opencode", "openai/gpt-6-luna") == "openai"
    assert AgentRecord.default_provider("opencode", None) is None
