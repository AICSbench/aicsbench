"""Agent authentication configuration.

Config files never contain secret *values*. They name host environment
variables and host files; the platform injects them into the trial container
at run time and never writes them to results or logs.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

from pydantic import Field, field_validator, model_validator

from ai_benchmark.config._base import StrictModel
from ai_benchmark.models.enums import AuthMode


class MountMode(str, Enum):
    COPY = "copy"
    """Copy the file into the container per trial. Token refreshes are discarded with it."""
    BIND_READ_ONLY = "bind_ro"
    BIND_READ_WRITE = "bind_rw"
    """Lets the agent persist refreshed tokens to the host file. Use only if refresh requires it."""


class CredentialFile(StrictModel):
    """A host credential file made available inside the trial container."""

    source: Path = Field(description="Host path; '~' is expanded.")
    target: str = Field(description="Absolute path inside the container.")
    mode: MountMode = MountMode.COPY

    @field_validator("target")
    @classmethod
    def _absolute_target(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("credential target must be an absolute container path")
        return v

    @property
    def host_path(self) -> Path:
        return self.source.expanduser()


class AgentAuthConfig(StrictModel):
    """How one agent authenticates, and which host secrets it needs."""

    mode: AuthMode = AuthMode.SUBSCRIPTION
    env: list[str] = Field(
        default_factory=list,
        description="Names of host environment variables passed into the container.",
    )
    env_from: dict[str, str] = Field(
        default_factory=dict,
        description="Container variable <- host variable, for when the names differ "
        "(e.g. a gateway key under ANTHROPIC_AUTH_TOKEN). Values are read from the host "
        "environment at container-create time and never recorded.",
    )
    files: list[CredentialFile] = Field(default_factory=list)

    @field_validator("env")
    @classmethod
    def _names_not_values(cls, v: list[str]) -> list[str]:
        for name in v:
            if not name.replace("_", "").isalnum() or not name[0].isalpha():
                raise ValueError(
                    f"'{name}' is not an environment variable name. "
                    "Put the secret in the host environment and list only its name."
                )
        return v

    @field_validator("env_from")
    @classmethod
    def _renames_not_values(cls, v: dict[str, str]) -> dict[str, str]:
        for container, host in v.items():
            for name in (container, host):
                if not name.replace("_", "").isalnum() or not name[0].isalpha():
                    raise ValueError(
                        f"'{name}' is not an environment variable name. "
                        "Map container variable names to host variable names; never a value."
                    )
        return v

    @model_validator(mode="after")
    def _has_credentials(self) -> AgentAuthConfig:
        if not self.env and not self.files and not self.env_from:
            raise ValueError("auth needs at least one environment variable or credential file")
        overlap = sorted(set(self.env) & set(self.env_from))
        if overlap:
            raise ValueError(
                f"{', '.join(overlap)} is both a passed-through and a renamed variable"
            )
        return self

    def merged_with(self, other: AgentAuthConfig) -> AgentAuthConfig:
        """The union of two configs' credentials (adds a custom endpoint's gateway variable)."""
        return AgentAuthConfig(
            mode=self.mode,
            env=[*self.env, *(name for name in other.env if name not in self.env)],
            env_from={**self.env_from, **other.env_from},
            files=[*self.files, *(file for file in other.files if file not in self.files)],
        )

    def missing(self, environ: dict[str, str] | None = None) -> list[str]:
        """Credentials that are absent on this host (names/paths only, never values)."""
        environ = dict(os.environ) if environ is None else environ
        problems = [f"environment variable {n} is not set" for n in self.env if not environ.get(n)]
        problems += [
            f"environment variable {host} (for container variable {container}) is not set"
            for container, host in self.env_from.items()
            if not environ.get(host)
        ]
        problems += [
            f"credential file {f.host_path} does not exist"
            for f in self.files
            if not f.host_path.is_file()
        ]
        return problems
