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
from ai_benchmark.pricing import ModelPrice, default_catalog


def _estimated_api_cost(trial: TrialResult) -> tuple[float, ModelPrice] | None:
    agent = trial.agent
    model = agent.resolve_model(agent.requested_model, agent.reported_model)
    price = default_catalog().lookup(model) if model else None
    tokens = trial.metrics.usage.tokens
    # OpenAI counts cache reads inside input and reasoning inside output. Older
    # Command Code reports follow its input convention without declaring a scheme.
    # OpenCode normalizes both the other way: its adapter adds cache reads and
    # writes back to input, and keeps reasoning separate from output.
    pricing_scheme = tokens.accounting_scheme
    if pricing_scheme is None and agent.name == "opencode":
        pricing_scheme = "opencode"
    elif pricing_scheme is None and agent.name == "commandcode":
        pricing_scheme = "openai"
    if price is None or pricing_scheme not in {"openai", "opencode"}:
        return None
    prompt_rate, completion_rate = price.prompt, price.completion
    if prompt_rate is None or completion_rate is None:
        return None
    total_input = tokens.input_tokens.value
    cached = tokens.cached_input_tokens.value
    output = tokens.output_tokens.value
    if total_input is None or cached is None or output is None:
        return None
    if not 0 <= cached <= total_input or output < 0:
        return None
    if pricing_scheme == "opencode":
        # OpenCode charges reasoning at the output rate. Its normalized output
        # excludes reasoning, so a missing count is unknown, not zero.
        reasoning = tokens.reasoning_tokens.value
        if reasoning is None or reasoning < 0:
            return None
        output += reasoning
    cached_rate = price.cache_read
    if cached_rate is None:
        # No listed cache-read rate: zero cached tokens need no rate, but any
        # cached tokens make the input cost unknown.
        if cached != 0:
            return None
        cached_rate = 0.0
    amount = (total_input - cached) * prompt_rate + cached * cached_rate + output * completion_rate
    # Avoid binary floating-point noise in JSON/CSV; 12 places preserve sub-cent costs.
    return round(amount, 12), price


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
    estimates: dict[str, list[tuple[float, ModelPrice]]] = {}
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
    used_cards: set[ModelPrice] = set()
    for row in rows:
        values = estimates.get(row.value, [])
        cards = {card for _, card in values}
        used_cards.update(cards)
        total = sum(amount for amount, _ in values) if values else None
        # All priced trials, including failures, fund each pass. A pass whose
        # own trial is unpriced still counts in the denominator.
        per_pass = total / row.passed if total is not None and row.passed else None
        data.append(
            {
                **asdict(row),
                "estimated_api_cost_usd": total,
                "estimated_api_cost_samples": len(values),
                "estimated_api_cost_per_pass_usd": per_pass,
                "estimated_api_cost_per_pass_partial": per_pass is not None
                and len(values) < row.trials,
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
        "Estimated API cost per pass (USD) | Estimated trials | Confidence |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row, group_data in zip(rows, data, strict=True):
        label = _markdown_label(row.value)
        mean = f"{row.mean_seconds:.2f}" if row.mean_seconds is not None else "n/a"
        dev = f"{row.stdev_seconds:.2f}" if row.stdev_seconds is not None else "n/a"
        cost = f"{row.mean_cost_usd:.4f}" if row.mean_cost_usd is not None else "n/a"
        total = group_data["estimated_api_cost_usd"]
        estimate_text = f"{total:.6f}" if total is not None else "n/a"
        per_pass = group_data["estimated_api_cost_per_pass_usd"]
        if per_pass is None:
            per_pass_text = "n/a"
        else:
            per_pass_text = f"{per_pass:.6f}"
            if group_data["estimated_api_cost_per_pass_partial"]:
                per_pass_text += " (partial)"
        lines.append(
            f"| {label} | "
            f"{row.passed}/{row.trials} | {row.rate_limited} | "
            f"{row.timed} | {mean} | {dev} | {cost} | {row.cost_samples} | "
            f"{estimate_text} | {per_pass_text} | "
            f"{group_data['estimated_api_cost_samples']}/{row.trials} | "
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
            "Estimated API cost uses OpenRouter listed base prompt, completion and cache-read "
            "rates from a dated snapshot as a fixed comparison baseline; it is not an official "
            "vendor price or an actual bill. Route, time-of-day and context-length overrides, "
            "cache-write and separate internal-reasoning rates, and non-token fees are not "
            "applied. Totals include all priced trials, even failures; unpriced trials are not "
            "counted as zero.",
            "Estimated API cost per pass divides that total by every pass, including passes whose "
            "own trial is unpriced. It is n/a without a pass or a priced trial, and marked partial "
            "when some trials are unpriced.",
            "The requested model is used when the CLI does not report one. Exact IDs and "
            "variants are priced separately; aliases are not guessed.",
            *[
                f"Rate card: [{card.label}]({card.source})."
                for card in sorted(used_cards, key=lambda card: card.label)
            ],
            "",
        ]
    )
    return "\n".join(lines)
