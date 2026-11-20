"""Experiment configuration: what a single ``aicsbench run`` executes."""

from __future__ import annotations

import ipaddress
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from ai_benchmark.config._base import SLUG_PATTERN, StrictModel
from ai_benchmark.config.auth import AgentAuthConfig
from ai_benchmark.config.task import Timeouts
from ai_benchmark.models.enums import AuthMode, ToolingMode


class EndpointConfig(StrictModel):
    """A custom model endpoint (an LLM gateway) one harness runs every request through.

    Config files never contain the key's value: ``api_key_env`` only names the
    host environment variable holding it, and it is injected into the trial
    container by that name (docs/phase-5/tasks/09-custom-endpoints.md).
    """

    provider: str = Field(
        pattern=SLUG_PATTERN,
        description="Slug naming the gateway (e.g. 'openrouter', 'litellm'); "
        "the value results and comparisons use.",
    )
    base_url: str = Field(
        description="https:// URL. Plain http:// is allowed only for localhost, a private "
        "IP or a Docker service name. No user info and no query string, so it cannot "
        "carry the key.",
    )
    api_key_env: str = Field(
        description="Name of the host environment variable holding the gateway key.",
    )
    wire_api: Literal["chat", "responses"] | None = Field(
        default=None,
        description="Codex only; ignored by the other adapters.",
    )

    @property
    def host(self) -> str:
        """The base URL's host name only: no scheme, port, path or credentials."""
        return urlsplit(self.base_url).hostname or ""

    @field_validator("api_key_env")
    @classmethod
    def _names_not_values(cls, v: str) -> str:
        if not v.replace("_", "").isalnum() or not v[0].isalpha():
            raise ValueError(
                f"'{v}' is not an environment variable name. "
                "Put the secret in the host environment and pass only its name."
            )
        return v

    @field_validator("base_url")
    @classmethod
    def _plain_http_only_for_private_hosts(cls, v: str) -> str:
        parts = urlsplit(v)
        if parts.scheme not in ("https", "http"):
            raise ValueError(
                "base_url must start with https:// (plain http:// is allowed only for "
                "localhost, a private IP or a Docker service name)"
            )
        if parts.username is not None or parts.password is not None:
            raise ValueError("base_url must not contain user info; it could carry the key")
        if parts.query or parts.fragment:
            raise ValueError(
                "base_url must not contain a query string or fragment; it could carry the key"
            )
        host = (parts.hostname or "").lower()
        if parts.scheme == "https":
            return v
        if host == "localhost" or "." not in host:  # bare host: a Docker service name
            return v
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError(
                f"plain http:// is only allowed for localhost, a private IP or a Docker "
                f"service name, not '{host}'; use https://"
            ) from None
        if not (address.is_private or address.is_loopback):
            raise ValueError(
                f"plain http:// is only allowed for localhost, a private IP or a Docker "
                f"service name, not the public address '{host}'; use https://"
            )
        return v


class AgentSelection(StrictModel):
    """One agent/model combination to benchmark."""

    agent: str = Field(pattern=SLUG_PATTERN, description="Registered adapter name.")
    model: str | None = Field(default=None, description="None = the agent's own default model.")
    reasoning_effort: str | None = None
    options: dict[str, Any] = Field(
        default_factory=dict, description="Adapter-specific options, validated by the adapter."
    )
    auth: AgentAuthConfig | None = Field(
        default=None,
        description="Override the adapter's default credentials for the experiment's auth mode.",
    )
    endpoint: EndpointConfig | None = Field(
        default=None,
        description="A custom model endpoint (gateway) this harness runs every request "
        "through. Requires auth mode 'api_key'.",
    )

    @classmethod
    def parse(cls, spec: str) -> AgentSelection:
        """Parse the CLI shorthand ``agent`` or ``agent:model``."""
        agent, _, model = spec.strip().partition(":")
        return cls(agent=agent, model=model or None)

    @property
    def key(self) -> str:
        return f"{self.agent}:{self.model}" if self.model else self.agent

    @model_validator(mode="after")
    def _endpoint_requires_api_key(self) -> AgentSelection:
        if (
            self.endpoint is not None
            and self.auth is not None
            and self.auth.mode is not AuthMode.API_KEY
        ):
            raise ValueError(
                "an endpoint requires auth mode 'api_key'; run it as a separate experiment"
            )
        return self


class McpServerConfig(StrictModel):
    name: str
    command: list[str] | None = None
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_transport(self) -> McpServerConfig:
        if (self.command is None) == (self.url is None):
            raise ValueError("an MCP server needs exactly one of 'command' or 'url'")
        return self


class ToolingConfig(StrictModel):
    mode: ToolingMode = ToolingMode.STANDARD
    enabled_tools: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _standard_has_no_mcp(self) -> ToolingConfig:
        if self.mode is ToolingMode.STANDARD and self.mcp_servers:
            raise ValueError("MCP servers are only allowed in 'best_ecosystem' tooling mode")
        return self


class ExperimentConfig(StrictModel):
    """A full experiment matrix: agents x stacks x trials for one task."""

    task: str = Field(pattern=SLUG_PATTERN)
    agents: list[AgentSelection] = Field(min_length=1)
    stacks: list[str] = Field(min_length=1)
    trials: int | None = Field(default=None, ge=1, description="None = the task's default.")
    tooling: ToolingConfig = Field(default_factory=ToolingConfig)
    auth_mode: AuthMode = Field(
        default=AuthMode.SUBSCRIPTION,
        description="Applies to every agent in the run, so results stay comparable.",
    )
    timeouts: Timeouts | None = Field(
        default=None, description="Overrides the task's timeouts for every trial."
    )
    randomize_order: bool = True
    seed: int | None = Field(default=None, description="None = a random seed, recorded in results.")
    max_parallel: int = Field(default=1, ge=1, le=8, description="Maximum trials running at once.")
    max_repairs: int = Field(
        default=0, ge=0, le=5, description="Extra agent attempts after failed acceptance tests."
    )
    stop_on_infra_error: bool = Field(
        default=False, description="Abort the run when the platform itself fails."
    )
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("stacks")
    @classmethod
    def _unique_stacks(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("stacks must be unique")
        return v

    @field_validator("agents")
    @classmethod
    def _unique_agents(cls, v: list[AgentSelection]) -> list[AgentSelection]:
        keys = [a.key for a in v]
        if len(set(keys)) != len(keys):
            raise ValueError("agent/model combinations must be unique")
        return v

    @model_validator(mode="after")
    def _single_auth_mode(self) -> ExperimentConfig:
        mixed = [a.key for a in self.agents if a.auth and a.auth.mode is not self.auth_mode]
        if mixed:
            raise ValueError(
                f"agents {', '.join(mixed)} override auth with a different mode than "
                f"auth_mode '{self.auth_mode.value}'; run each auth mode as a separate experiment"
            )
        gatewayed = [
            a.key
            for a in self.agents
            if a.endpoint is not None and self.auth_mode is not AuthMode.API_KEY
        ]
        if gatewayed:
            raise ValueError(
                f"agents {', '.join(gatewayed)} use an endpoint, which requires auth_mode "
                f"'api_key'; run each auth mode as a separate experiment"
            )
        return self
