"""USD per million tokens, for the session cost meter (Anthropic first-party API rates).

Cache writes (5-minute TTL) bill at 1.25x input. Update if pricing changes.
"""

PRICES = {
    #                     input  output  cache_read
    "claude-opus-5-5":   (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5":  (0.10, 0.50, 0.01),
    # Possible server-side fallback targets
    "claude-opus-4-8":   (5.00, 25.00, 0.50),
    "claude-opus-5":     (5.00, 25.00, 0.50),
}

MODELS = [
    {"id": "claude-haiku-5-5", "label": "Haiku 5.5 (cheapest, fast)"},
    {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5"},
    {"id": "claude-opus-5-5", "label": "Opus 5.5 (strongest, slowest)"},
]


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    inp, out, read = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (input_tokens * inp + output_tokens * out + cache_read * read + cache_write * inp * 1.25) / 1_000_000
