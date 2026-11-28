"""Experiment configuration: what a single ``aicsbench run`` executes."""

from __future__ import annotations

import contextlib
import ipaddress
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from aicsbench.config._base import SLUG_PATTERN, StrictModel
from aicsbench.config.auth import AgentAuthConfig
from aicsbench.config.task import Timeouts
from aicsbench.models.enums import AuthMode, ToolingMode


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
        if not v or not v.replace("_", "").isalnum() or not v[0].isalpha():
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
        if not host:
            raise ValueError("base_url needs a host name")
        try:
            # Raises for a non-numeric or out-of-range port (urllib.parse).
            port = parts.port
        except ValueError:
            raise ValueError(f"base_url has an invalid port: '{v}'") from None
        if port is not None and not 1 <= port <= 65535:
            raise ValueError(f"base_url has an invalid port: '{v}'")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
            hex_host = host.startswith(("0x", "0X")) and all(
                c in "0123456789abcdefABCDEF" for c in host[2:]
            )
        else:
            hex_host = False
        numeric_host = host.isascii() and (host.isdigit() or hex_host)
        if numeric_host:
            # WHATWG URL parsing (what HTTP clients use) reads dotless integer
            # hosts as IPv4: decimal ('16909060'), hexadecimal ('0x01020304'),
            # or octal when a leading zero is present ('0167772160' is not
            # 10.0.0.0). Classify with those rules so the validator agrees
            # with where the client will actually connect.
            base = 16 if hex_host else (8 if host.startswith("0") and len(host) > 1 else 10)
            with contextlib.suppress(ValueError):
                address = ipaddress.ip_address(int(host, base))
        if parts.scheme == "https":
            return v
        if address is not None:
            if not (address.is_private or address.is_loopback):
                raise ValueError(
                    f"plain http:// is only allowed for localhost, a private IP or a Docker "
                    f"service name, not the public address '{host}'; use https://"
                )
            return v
        if numeric_host:
            # A numeric host the parser could not classify (e.g. beyond the
            # IPv4 range) is not a Docker service name either; refuse plain
            # http instead of letting it through under the bare-name rule.
            raise ValueError(
                f"plain http:// is only allowed for localhost, a private IP or a Docker "
                f"service name, not the numeric host '{host}'; use https://"
            )
        # A bare host is a Docker service name. A host containing percent
        # escapes is not one: WHATWG URL parsing percent-decodes the host
        # before connecting, so '%38%2e%38...' is really '8.8.8.8'. A host
        # with non-ASCII characters is not one either: WHATWG host parsing
        # maps Unicode dot separators (U+3002, U+FF0E, U+FF61) to '.', so
        # '8。8。8。8' is really '8.8.8.8'.
        if (host == "localhost" or "." not in host) and "%" not in host and host.isascii():
            return v
        raise ValueError(
            f"plain http:// is only allowed for localhost, a private IP or a Docker "
            f"service name, not '{host}'; use https://"
        )


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
        key = f"{self.agent}:{self.model}" if self.model else self.agent
        if self.endpoint is not None:
            # Two selections differing only in their endpoint are different
            # trials: without this they collide in the duplicate check and
            # their trial IDs would overwrite each other's results. The full
            # address (scheme, host, port and path) separates endpoints that
            # differ only in port or path.
            return f"{key}@{self.endpoint.provider}:{self.endpoint.base_url}"
        return key

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

    @model_validator(mode="after")
    def _endpoint_needs_the_gateway_model(self) -> AgentSelection:
        """OpenCode's model form is ``--model <provider>/<model>`` (required form).

        With a custom endpoint the prefix must name the endpoint's provider, or
        OpenCode selects another provider and bypasses the gateway. The other
        harnesses keep their own model forms (Codex and Claude Code take the
        bare model ID even through a gateway).
        """
        if self.agent != "opencode" or self.endpoint is None:
            return self
        provider, separator, model = (self.model or "").partition("/")
        if not separator or not model:
            raise ValueError(
                f"an opencode selection with an endpoint needs a model in the form "
                f"'{self.endpoint.provider}/<model>', got {self.model!r}"
            )
        if provider != self.endpoint.provider:
            raise ValueError(
                f"opencode model '{self.model}' selects provider '{provider}', but the "
                f"endpoint provider is '{self.endpoint.provider}'; the model must be "
                f"'{self.endpoint.provider}/<model>' so the run goes through the gateway"
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
