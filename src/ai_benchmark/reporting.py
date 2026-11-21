"""Flat trial exports and aggregate report rendering."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict
from html import escape
from typing import Any

from ai_benchmark.comparison import ComparisonRow
from ai_benchmark.models.enums import ComparisonDimension
from ai_benchmark.models.results import TrialResult

# USD per million uncached input, cached input, and output tokens.
_LUNA_RATES = (0.10, 0.01, 0.50)
_LUNA_RATE_CARD = "openai/gpt-6-luna/standard-short-context@2026-11-13"
_PRICE_SOURCE = "https://developers.openai.com/api/docs/pricing"


def _estimated_api_cost(trial: TrialResult) -> float | None:
    agent = trial.agent
    model = agent.resolve_model(agent.requested_model, agent.reported_model)
    tokens = trial.metrics.usage.tokens
    if (
        (agent.name, agent.provider, model) != ("codex", "openai", "gpt-6-luna")
        or agent.endpoint_host is not None
        or tokens.accounting_scheme != "openai"
    ):
        return None
    total_input = tokens.input_tokens.value
    cached = tokens.cached_input_tokens.value
    output = tokens.output_tokens.value
    if total_input is None or cached is None or output is None:
        return None
    if not 0 <= cached <= total_input or output < 0:
        return None
    uncached_rate, cached_rate, output_rate = _LUNA_RATES
    return (
        (total_input - cached) * uncached_rate + cached * cached_rate + output * output_rate
    ) / 1e6


def trial_row(trial: TrialResult) -> dict[str, str | int | float | bool | None]:
    """One stable, dashboard-friendly row; unavailable metrics remain null."""
    performance = trial.metrics.performance
    quality = trial.metrics.quality
    usage = trial.metrics.usage
    tokens = usage.tokens
    return {
        "run_id": trial.run_id,
        "trial_id": trial.trial_id,
        "trial_index": trial.trial_index,
        "max_repairs": trial.max_repairs,
        "max_parallel": trial.max_parallel,
        "task": trial.task_id,
        "task_version": trial.task_version,
        "agent": trial.agent.name,
        "model": trial.agent.model_label,
        "stack": trial.environment.stack,
        "language": trial.environment.language,
        "language_version": trial.environment.language_version,
        "framework": trial.environment.framework,
        "framework_version": trial.environment.framework_version,
        "tooling_mode": trial.tooling.mode.value,
        "auth_mode": trial.agent.auth_mode.value,
        "status": trial.status.value,
        "rate_limited": trial.rate_limited,
        "agent_seconds": performance.agent_execution_seconds.value,
        "wall_seconds": performance.total_wall_clock_seconds.value,
        "tests_passed": quality.tests_passed.value,
        "tests_failed": quality.tests_failed.value,
        "tests_total": quality.tests_total.value,
        "repair_iterations": quality.repair_iterations.value,
        "token_scheme": tokens.accounting_scheme,
        "input_tokens": tokens.input_tokens.value,
        "cached_input_tokens": tokens.cached_input_tokens.value,
        "output_tokens": tokens.output_tokens.value,
        "reasoning_tokens": tokens.reasoning_tokens.value,
        "total_tokens": tokens.total_tokens.value,
        "cost_usd": usage.cost.value,
        "cost_availability": usage.cost.availability.value,
        "harness": trial.agent.name,
        "harness_version": trial.agent.version,
        "requested_model": trial.agent.requested_model,
        "reasoning_effort": trial.agent.reasoning_effort,
        "language_version_installed": trial.environment.installed_versions.get("language"),
        "framework_version_installed": trial.environment.installed_versions.get("framework"),
        "provider": trial.agent.provider,
        "endpoint_host": trial.agent.endpoint_host,
        "framework_version_changed": trial.environment.framework_version_changed,
    }


def csv_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(
        {
            key: "'" + value
            if isinstance(value, str)
            and (
                value.startswith(("\t", "\r", "\n"))
                or value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
            )
            else value
            for key, value in row.items()
        }
        for row in rows
    )
    return stream.getvalue()


def export_trials(trials: list[TrialResult], fmt: str) -> str:
    rows = []
    for trial in trials:
        estimate = _estimated_api_cost(trial)
        rows.append(
            {
                **trial_row(trial),
                "estimated_api_cost_usd": estimate,
                "estimated_api_cost_rate_card": _LUNA_RATE_CARD if estimate is not None else None,
                "estimated_api_cost_price_source": _PRICE_SOURCE if estimate is not None else None,
                "estimated_api_cost_model_assumed": (
                    not trial.agent.reported_model.value if estimate is not None else None
                ),
            }
        )
    if fmt == "csv":
        return csv_text(rows)
    if fmt == "jsonl":
        return "".join(json.dumps(row, allow_nan=False) + "\n" for row in rows)
    raise ValueError("format must be csv or jsonl")


def _markdown_label(value: str) -> str:
    return escape(value).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")


def render_report(
    rows: list[ComparisonRow], dimension: ComparisonDimension, fmt: str, trials: list[TrialResult]
) -> str:
    estimates: dict[str, list[float]] = {}
    for trial in trials:
        estimate = _estimated_api_cost(trial)
        if estimate is not None:
            group = trial.dimension_value(dimension)
            estimates.setdefault(group, []).append(estimate)
    data = []
    for row in rows:
        values = estimates.get(row.value, [])
        data.append(
            {
                **asdict(row),
                "estimated_api_cost_usd": sum(values) if values else None,
                "estimated_api_cost_samples": len(values),
                "estimated_api_cost_rate_card": _LUNA_RATE_CARD if values else None,
                "estimated_api_cost_price_source": _PRICE_SOURCE if values else None,
            }
        )
    if fmt == "json":
        return (
            json.dumps({"compare_by": dimension.value, "groups": data}, indent=2, allow_nan=False)
            + "\n"
        )
    if fmt == "csv":
        return csv_text(data)
    if fmt != "markdown":
        raise ValueError("format must be markdown, json, or csv")
    lines = [
        f"# Benchmark comparison by {dimension.value}",
        "",
        "| Group | Passed/trials | Rate limited | Timed | Mean time (s) | "
        "Std dev (s) | Mean cost (USD) | Cost samples | Estimated API cost (USD, total) | "
        "Estimated trials | Confidence |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row, group_data in zip(rows, data, strict=True):
        label = _markdown_label(row.value)
        mean = f"{row.mean_seconds:.2f}" if row.mean_seconds is not None else "n/a"
        dev = f"{row.stdev_seconds:.2f}" if row.stdev_seconds is not None else "n/a"
        cost = f"{row.mean_cost_usd:.4f}" if row.mean_cost_usd is not None else "n/a"
        total = group_data["estimated_api_cost_usd"]
        estimate_text = f"{total:.6f}" if total is not None else "n/a"
        lines.append(
            f"| {label} | "
            f"{row.passed}/{row.trials} | {row.rate_limited} | "
            f"{row.timed} | {mean} | {dev} | {cost} | {row.cost_samples} | "
            f"{estimate_text} | {group_data['estimated_api_cost_samples']}/{row.trials} | "
            f"{'low' if row.low_confidence else 'ok'} |"
        )
    lines.extend(["", "## Success rate", "", "| Group | Success |", "| --- | --- |"])
    for row in rows:
        rate = row.passed / row.trials if row.trials else 0
        label = _markdown_label(row.value)
        lines.append(f"| {label} | {'█' * round(rate * 20)} {rate:.0%} |")
    lines.extend(
        [
            "",
            "Agent time uses passing trials without rate-limit events and an available timer.",
            "Success rate includes all trials. Cost is n/a when unavailable.",
            "Estimated API cost is not a charge. Totals include all priced trials, even failures; "
            "unpriced trials are not counted as zero.",
            f"Rate card: [{_LUNA_RATE_CARD}]({_PRICE_SOURCE}). Assumes Standard short-context "
            "pricing and the requested model when the CLI does not report one.",
            "",
        ]
    )
    return "\n".join(lines)
