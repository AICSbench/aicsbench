"""Agent adapter interface, registry and the built-in adapters."""

from aicsbench.agents.base import (
    AgentAdapter,
    AgentAvailability,
    AgentCapabilities,
    AgentRunResult,
    AgentTask,
)
from aicsbench.agents.registry import AgentRegistry, default_registry, register_agent
from aicsbench.environment.base import AgentImageLayer, CommandRunner

__all__ = [
    "AgentAdapter",
    "AgentAvailability",
    "AgentCapabilities",
    "AgentImageLayer",
    "AgentRegistry",
    "AgentRunResult",
    "AgentTask",
    "CommandRunner",
    "default_registry",
    "register_agent",
]

# Built-in adapters register themselves in ``default_registry`` on import.
from aicsbench.agents import agy, amp, claude_code, codex, commandcode, opencode  # noqa: E402, F401
