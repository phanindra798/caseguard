"""Spotting instruction-like text in UNTRUSTED content (customer messages, customer notes, ...).

Pattern matching can't catch every attack. It is one layer: the real protection is that untrusted
text is passed to the model as labelled data, the model can't call write tools, code decides
eligibility and approvals, and the output guard checks every reply against evidence.
"""
from __future__ import annotations

import re

_PATTERNS = [
    r"ignore (?:all |any |the )?(?:previous|prior|above) (?:instructions|rules|policies)",
    r"system (?:note|prompt|message)",
    r"you are now",
    r"developer mode",
    r"skip the approval",
    r"already approved",
    r"tell the customer",
    r"escalate (?:it|this|every|all)\b.*\b(?:immediately|now)",
]
_RX = [re.compile(p, re.I) for p in _PATTERNS]


def scan(text: str | None) -> list[str]:
    """The instruction-like phrases found in `text` (empty list = nothing suspicious)."""
    if not text:
        return []
    return [m.group(0) for rx in _RX if (m := rx.search(text))]
