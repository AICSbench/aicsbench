"""Bundle metadata models (see docs/phase-5/schemas.md#submissionjson)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

BUNDLE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
SUITE_HASH_PATTERN = r"^[0-9a-f]{64}$"


class Submitter(BaseModel):
    """Who submits. ``github`` is the GitHub handle without ``@``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    github: str | None = None

    @field_validator("github")
    @classmethod
    def _github_without_at(cls, value: str | None) -> str | None:
        if value is not None and value.startswith("@"):
            raise ValueError("github must not include a leading '@'")
        return value


class SubmissionInfo(BaseModel):
    """``submission.json``: the one file that describes the whole bundle."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int
    run_id: str = Field(pattern=BUNDLE_ID_PATTERN)
    task_id: str = Field(pattern=BUNDLE_ID_PATTERN)
    task_version: str
    submitted_at: datetime
    submitter: Submitter
    notes: str = Field(default="", max_length=2000)
    platform_version: str
    platform_git_commit: str | None = None
    acceptance_suite_sha256: str = Field(pattern=SUITE_HASH_PATTERN)
    suite_hash_source: Literal["run", "submit"]
    logs_included: bool = False
    files: dict[str, str] = Field(
        description="Relative path -> SHA-256 (hex) of every other file in the bundle.",
    )

    @field_validator("schema_version")
    @classmethod
    def _schema_version_is_1(cls, value: int) -> int:
        if value != 1:
            raise ValueError("schema_version must be 1")
        return value

    @field_validator("submitted_at")
    @classmethod
    def _submitted_at_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("submitted_at must be UTC (explicit zero UTC offset)")
        return value


@dataclass(frozen=True)
class Problem:
    """One validation finding. An empty problem list means the bundle is valid."""

    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.code} {self.path}: {self.message}"
