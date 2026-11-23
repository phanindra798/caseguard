"""Spotting instruction-like text in UNTRUSTED content (customer messages, customer notes, ...).

Pattern matching can't catch every attack. It is one layer: the real protection is that untrusted
text is passed to the model as labelled data, the model can't call write tools, code decides
eligibility and approvals, and the output guard checks every reply against evidence.
"""
from __future__ import annotations

import re

# High precision: clear attempts to override the agent. Enough to BLOCK a customer's message.
_BLOCK = [
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions|rules|policies|guidance)",
    r"system (?:note|prompt|message|override)",
    r"you are now",
    r"developer mode",
]
# Broader: suspicious in DATA (e.g. a note telling the agent what to do), but ordinary in a customer's
# own words ("escalate it now!"). These only FLAG; they never block a customer.
_FLAG_ONLY = [
    r"skip the approval",
    r"already approved",
    r"tell the customer",
    r"escalate (?:it|this|every|all)\b.*\b(?:immediately|now)",
]
_RX_BLOCK = [re.compile(p, re.I) for p in _BLOCK]
_RX_ALL = _RX_BLOCK + [re.compile(p, re.I) for p in _FLAG_ONLY]


def scan(text: str | None, *, block_only: bool = False) -> list[str]:
    """Instruction-like phrases found in `text` (empty = nothing suspicious).
    block_only=True: only the high-precision patterns, for deciding whether to BLOCK a message."""
    if not text:
        return []
    return [m.group(0) for rx in (_RX_BLOCK if block_only else _RX_ALL) if (m := rx.search(text))]
