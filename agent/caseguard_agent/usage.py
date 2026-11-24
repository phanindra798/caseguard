"""Token and cost accounting for language-model calls.

Live models report real token counts (LangChain's provider-neutral `usage_metadata`). The scripted
brain makes no model calls, but it records an ESTIMATE of what the same prompts would cost a live
model (characters / 4, the usual rough rule for English text), clearly marked as estimated.

Prices are never hardcoded: set CASEGUARD_PRICE_INPUT_PER_1M and CASEGUARD_PRICE_OUTPUT_PER_1M (USD per
million tokens) for the model you use; without them, cost is reported as unknown.
"""
from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated: bool = False

    def cost_usd(self) -> float | None:
        price_in = os.getenv("CASEGUARD_PRICE_INPUT_PER_1M")
        price_out = os.getenv("CASEGUARD_PRICE_OUTPUT_PER_1M")
        if price_in is None or price_out is None:
            return None
        return (self.input_tokens * float(price_in) + self.output_tokens * float(price_out)) / 1_000_000


class UsageMeter:
    """Accumulates usage across calls; thread-safe. Take snapshots to get the usage of one turn."""

    def __init__(self):
        self._lock = threading.Lock()
        self.total = Usage()

    def record(self, input_tokens: int, output_tokens: int, *, estimated: bool = False) -> None:
        with self._lock:
            self.total.calls += 1
            self.total.input_tokens += input_tokens
            self.total.output_tokens += output_tokens
            self.total.estimated = self.total.estimated or estimated

    def snapshot(self) -> Usage:
        with self._lock:
            return Usage(self.total.calls, self.total.input_tokens, self.total.output_tokens, self.total.estimated)

    @staticmethod
    def delta(before: Usage, after: Usage) -> Usage:
        return Usage(after.calls - before.calls, after.input_tokens - before.input_tokens,
                     after.output_tokens - before.output_tokens, after.estimated)
