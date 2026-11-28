"""Auth-mode policies applied to collected metrics.

Kept separate from adapters so every agent is treated identically.
"""

from __future__ import annotations

from aicsbench.models.enums import AuthMode
from aicsbench.models.metrics import Metric, TrialMetrics

SUBSCRIPTION_COST_NOTE = "subscription auth: no per-run cost is billed"
CUSTOM_ENDPOINT_COST_NOTE = "custom endpoint; harness price table does not apply"
REPORTED_COST_KEY = "agent_reported_cost_usd"


def apply_auth_policy(
    metrics: TrialMetrics, mode: AuthMode, *, custom_endpoint: bool = False
) -> TrialMetrics:
    """Return metrics adjusted for how the agent was billed.

    Under a subscription nothing is billed per run, and through a custom
    endpoint the harness's own price table does not apply to the gateway, so
    ``usage.cost`` becomes unavailable. If the agent still printed a cost
    figure (some CLIs show an API-price equivalent), it is kept verbatim in
    ``provider_specific`` so no data is lost, but it is never presented as the
    run's cost.
    """
    if mode is not AuthMode.SUBSCRIPTION and not custom_endpoint:
        return metrics
    cost = metrics.usage.cost
    provider_specific = dict(metrics.provider_specific)
    if cost.is_available:
        provider_specific[REPORTED_COST_KEY] = cost.value
    note = SUBSCRIPTION_COST_NOTE if mode is AuthMode.SUBSCRIPTION else CUSTOM_ENDPOINT_COST_NOTE
    usage = metrics.usage.model_copy(
        update={"cost": Metric[float].unavailable(note=note, unit="USD")}
    )
    return metrics.model_copy(update={"usage": usage, "provider_specific": provider_specific})
