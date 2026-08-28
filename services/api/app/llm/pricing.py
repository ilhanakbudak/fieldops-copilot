"""What a call cost.

Prices are per million tokens, in USD, and they are applied at write time rather
than derived on read. Prices change; what a call cost on the day it ran does
not, and a cost dashboard that silently rewrites history is worse than no
dashboard.

An unknown model prices at zero rather than raising. Refusing to answer an
employee's question because a price list is out of date would be a poor trade,
and a zero in the dashboard is visible enough to get the table updated.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.base import Usage


@dataclass(frozen=True, slots=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float
    # Cached input is billed at a discount. The exact ratio varies by provider;
    # these are the published multipliers at the time of writing.
    cached_input_per_mtok: float = 0.0


PRICES: dict[str, Price] = {
    "gpt-5.6-terra": Price(input_per_mtok=2.50, output_per_mtok=10.00, cached_input_per_mtok=0.25),
    "gpt-5.6-luna": Price(input_per_mtok=0.15, output_per_mtok=0.60, cached_input_per_mtok=0.015),
    "text-embedding-3-small": Price(input_per_mtok=0.02, output_per_mtok=0.0),
    # Local models and the demo provider cost nothing, and saying so explicitly
    # is better than falling through to the unknown-model case.
    "fastembed": Price(0.0, 0.0),
    "mock": Price(0.0, 0.0),
}


def cost_usd(model: str, usage: Usage) -> float:
    price = PRICES.get(model)
    if price is None:
        return 0.0

    billable_input = max(usage.input_tokens - usage.cached_input_tokens, 0)
    return (
        billable_input * price.input_per_mtok
        + usage.cached_input_tokens * price.cached_input_per_mtok
        + usage.output_tokens * price.output_per_mtok
    ) / 1_000_000
