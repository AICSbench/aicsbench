from pathlib import Path

import pytest

from aicsbench.agents import AgentAdapter, AgentAvailability, AgentRegistry, AgentTask
from aicsbench.errors import AgentNotFoundError, ConfigurationError
from aicsbench.models import AuthMode

from .fakes import FakeAdapter, ScriptedRunner


def registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg._entry_points_loaded = True
    return reg


def task(tmp_path: Path) -> AgentTask:
    return AgentTask(
        trial_id="t1",
        prompt="build it",
        workdir="/workspace",
        log_dir=tmp_path,
        timeout_seconds=60,
        auth_mode=AuthMode.SUBSCRIPTION,
    )


def test_abstract_adapter_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        AgentAdapter()  # type: ignore[abstract]


def test_registry_register_and_create() -> None:
    reg = registry()
    reg.register(FakeAdapter)
    assert reg.names() == ["fake"]
    assert "fake" in reg
    adapter = reg.create("fake", {"x": 1})
    assert adapter.options == {"x": 1}


def test_registry_discovers_legacy_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "legacy_plugin.py").write_text(
        "from ai_benchmark.agents import AgentAdapter\n"
        "\n"
        "class LegacyAdapter(AgentAdapter):\n"
        "    name = 'legacy-plugin'\n"
    )
    metadata = tmp_path / "legacy_plugin-1.0.dist-info"
    metadata.mkdir()
    (metadata / "entry_points.txt").write_text(
        "[ai_benchmark.agents]\nlegacy-plugin = legacy_plugin:LegacyAdapter\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    assert AgentRegistry().get_class("legacy-plugin").__bases__ == (AgentAdapter,)


def test_registry_rejects_conflicts_and_unknown() -> None:
    reg = registry()
    reg.register(FakeAdapter)
    other = type("Other", (FakeAdapter,), {"name": "fake"})
    with pytest.raises(ConfigurationError):
        reg.register(other)
    with pytest.raises(AgentNotFoundError):
        reg.get_class("missing")


def test_adapter_executes_only_through_runner(tmp_path: Path) -> None:
    runner = ScriptedRunner(stdout="fake 1.0.0\n")
    adapter = FakeAdapter()
    assert adapter.check_availability(runner).version == "fake 1.0.0"
    result = adapter.run(task(tmp_path), runner)
    assert result.succeeded and not result.stopped_by_rate_limit
    assert runner.calls[-1] == ["fake", "--prompt", "build it"]


def test_rate_limit_is_surfaced(tmp_path: Path) -> None:
    runner = ScriptedRunner(stderr="Error: usage limit reached", exit_code=1)
    result = FakeAdapter().run(task(tmp_path), runner)
    assert result.stopped_by_rate_limit and not result.succeeded


def test_image_layer_and_default_auth() -> None:
    adapter = FakeAdapter()
    assert adapter.image_layer().tag_suffix == "fake-1.0.0"
    assert adapter.default_auth(AuthMode.SUBSCRIPTION).mode is AuthMode.SUBSCRIPTION


def test_availability_unknown_auth_is_ready() -> None:
    assert AgentAvailability(installed=True, authenticated=None).ready
    assert not AgentAvailability(installed=True, authenticated=False).ready
    assert not AgentAvailability(installed=False).ready
