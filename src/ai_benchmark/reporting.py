"""Flat trial exports and aggregate report rendering."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict
from html import escape
from typing import Any, NamedTuple

from ai_benchmark.comparison import ComparisonRow
from ai_benchmark.models.enums import ComparisonDimension
from ai_benchmark.models.results import TrialResult


class _RateCard(NamedTuple):
    label: str
    source: str
    # USD per million uncached input, cached input, and output tokens.
    rates: tuple[float, float, float]


_RATE_CARDS = {
    "gpt-6-luna": _RateCard(
        "openai/gpt-6-luna/standard-short-context@2026-11-13",
        "https://developers.openai.com/api/docs/pricing",
        (0.10, 0.01, 0.50),
    ),
    "glm-5.3-flash": _RateCard(
        "z-ai/glm-5.3-flash/standard@2026-11-21",
        "https://docs.z.ai/guides/overview/pricing",
        (0.15, 0.03, 0.50),
    ),
    "deepseek-v4.1-flash": _RateCard(
        "deepseek/deepseek-v4.1-flash/peak@2026-11-21",
        "https://api-docs.deepseek.com/quick_start/pricing/",
        (0.30, 0.006, 1.20),
    ),
}
_RATE_CARDS["deepseek-v4.1-flash-1017"] = _RATE_CARDS["deepseek-v4.1-flash"]
_RATE_CARDS["deepseek-flash"] = _RATE_CARDS["deepseek-v4.1-flash"]


def _estimated_api_cost(trial: TrialResult) -> tuple[float, _RateCard] | None:
    agent = trial.agent
    model = agent.resolve_model(agent.requested_model, agent.reported_model)
    card = _RATE_CARDS.get(model.lower().rsplit("/", 1)[-1]) if model else None
    tokens = trial.metrics.usage.tokens
    # Both formats include cache reads in input. Older Command Code reports don't
    # declare a scheme; don't assume the same convention for other adapters.
    if card is None or not (
        tokens.accounting_scheme == "openai"
        or (tokens.accounting_scheme is None and agent.name == "commandcode")
    ):
        return None
    total_input = tokens.input_tokens.value
    cached = tokens.cached_input_tokens.value
    output = tokens.output_tokens.value
    if total_input is None or cached is None or output is None:
        return None
    if not 0 <= cached <= total_input or output < 0:
        return None
    uncached_rate, cached_rate, output_rate = card.rates
    amount = (
        (total_input - cached) * uncached_rate + cached * cached_rate + output * output_rate
    ) / 1e6
    return amount, card


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
                "estimated_api_cost_usd": estimate[0] if estimate is not None else None,
                "estimated_api_cost_rate_card": estimate[1].label if estimate is not None else None,
                "estimated_api_cost_price_source": estimate[1].source if estimate else None,
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
    estimates: dict[str, list[tuple[float, _RateCard]]] = {}
    for trial in trials:
        estimate = _estimated_api_cost(trial)
        if estimate is not None:
            group = (
                f"{trial.agent.name} + {trial.agent.model_label}"
                if dimension is ComparisonDimension.HARNESS_MODEL
                else trial.dimension_value(dimension)
            )
            estimates.setdefault(group, []).append(estimate)
    data = []
    used_cards: set[_RateCard] = set()
    for row in rows:
        values = estimates.get(row.value, [])
        cards = {card for _, card in values}
        used_cards.update(cards)
        data.append(
            {
                **asdict(row),
                "estimated_api_cost_usd": sum(amount for amount, _ in values) if values else None,
                "estimated_api_cost_samples": len(values),
                "estimated_api_cost_rate_card": "; ".join(sorted({c.label for c in cards})) or None,
                "estimated_api_cost_price_source": "; ".join(sorted({c.source for c in cards}))
                or None,
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
            "Estimated API cost uses official model API rates, not actual charges. Totals include "
            "all priced trials, even failures; unpriced trials are not counted as zero.",
            "The requested model is used when the CLI does not report one.",
            *[f"Rate card: [{card.label}]({card.source})." for card in sorted(used_cards)],
            "",
        ]
    )
    return "\n".join(lines)
