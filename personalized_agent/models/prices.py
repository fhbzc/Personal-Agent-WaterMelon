"""WHAT A CALL COSTS, at the list price of the model that answered.

Where the provider says what a call cost - Claude Code does, for each call,
at Anthropic's list price; OpenRouter does, as billed - that is the cost.
Otherwise it is worked out here from the tokens, for a model whose price is
known: Anthropic's models, at their API list prices. A model not here has no
price, and its calls no cost - left empty rather than guessed.

On a subscription (Claude Code, Codex) the cost is what the same calls would
cost at list price: the subscription is what is paid, not this.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

#: USD per million tokens: (input, output, read from the cache). Written to
#: the cache: the input price times `write_times` (a quarter more for five
#: minutes, twice for an hour). Anthropic's list prices, October 2026.
PRICES: Dict[str, Tuple[float, float, float]] = {
    "claude-fable-5-1": (10.00, 50.00, 0.25),
    "claude-mythos-5-1": (10.00, 50.00, 0.25),
    "claude-fable-5": (10.00, 50.00, 1.00),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-opus-4-7": (5.00, 25.00, 0.50),
    "claude-opus-4-6": (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-sonnet-4-6": (3.00, 15.00, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}

#: Claude Haiku 5.5 above this many tokens of prompt: five times the price.
HAIKU_TIER = ("claude-haiku-5-5", 100_000, 5.0)

#: Claude Code's names for its models, as it answers them today
#: (`claude -p --model haiku` answers as claude-haiku-5-5).
from .providers.claude_code_provider import ALIASES  # noqa: E402


def known(model: str) -> Optional[str]:
    """The priced model a name means, if any: an id, an alias, or an id
    with a provider's prefix ("anthropic/claude-sonnet-5-5") or a date."""
    name = str(model or "").strip().lower()
    name = ALIASES.get(name, name)
    name = name.split("/")[-1].replace(".", "-")
    if name in PRICES:
        return name
    for one in sorted(PRICES, key=len, reverse=True):
        if name.startswith(one + "-"):
            return one
    return None


def estimate(model: str, input_tokens: int, output_tokens: int,
             cached_input_tokens: int = 0, cache_written_tokens: int = 0,
             write_times: float = 1.25) -> Optional[float]:
    """The call's cost in USD at list price, or None for a model not priced."""
    name = known(model)
    if name is None:
        return None
    price_in, price_out, price_read = PRICES[name]
    prompt = int(input_tokens or 0) + int(cached_input_tokens or 0) + int(cache_written_tokens or 0)
    if name == HAIKU_TIER[0] and prompt > HAIKU_TIER[1]:
        price_in, price_out, price_read = (p * HAIKU_TIER[2] for p in (price_in, price_out, price_read))
    return round((int(input_tokens or 0) * price_in
                  + int(cache_written_tokens or 0) * price_in * write_times
                  + int(cached_input_tokens or 0) * price_read
                  + int(output_tokens or 0) * price_out) / 1e6, 9)


def cache_write_times(provider: str) -> float:
    """How much more than input a token written to the cache costs: Claude
    Code keeps its cache for an hour (twice the input price), Anthropic's
    API here for five minutes (a quarter more)."""
    return 2.0 if provider in ("claude_code", "claude_desktop") else 1.25
