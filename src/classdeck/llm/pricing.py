"""Per-model price table. Intentionally empty: the docs do not list prices on the model page.

Fill it in from the billing page as prices become known. An unknown price yields ``None``,
never a guess.
"""

from __future__ import annotations

from classdeck.llm.base import Usage

# model ID -> (USD per 1M input tokens, USD per 1M output tokens)
PRICES: dict[str, tuple[float, float]] = {
    # OpenRouter list prices, read on 2026-10-28 (USD per 1M input / output tokens).
    # Add the judge model you configure; unknown models report cost_usd = None, never a guess.
    "openai/gpt-6-luna": (0.10, 0.50),
    "openai/gpt-6-luna-pro": (0.10, 0.50),
    "openai/gpt-5.6-luna": (0.20, 1.20),
    "deepseek/deepseek-v4.1-flash-1017": (0.30, 1.20),
    "deepseek/deepseek-v4.1-flash": (0.14, 0.42),
    "deepseek/deepseek-v4-flash": (0.14, 0.28),
}


def estimate_cost_usd(model: str, usage: Usage) -> float | None:
    price = PRICES.get(model)
    if price is None or usage.input_tokens is None or usage.output_tokens is None:
        return None
    input_price, output_price = price
    return (usage.input_tokens * input_price + usage.output_tokens * output_price) / 1_000_000
