"""What does the customer want: an escalation, information, or not clear yet?

Used by the scripted brain (a live model judges intent itself). Built from the DEV failures of the
evaluation; the holdout split was not used to choose these words.

Rule: an explicit request (escalate, chase, specialist, ...) means escalation. Lateness on its own
also means escalation, UNLESS the message is a question ("Is order 1042 late?"): then the customer
is asking for information, and escalating would be an unwanted write.
"""
from __future__ import annotations

import re

_REQUEST = re.compile(r"escalat|\bchase\b|push .{0,20}up the chain|\bspecialist\b|someone senior|"
                      r"raise (?:a )?complaint|look into it", re.I)
_LATE = re.compile(r"\blate\b|overdue|delay|never (?:arrived|came|showed up)|"
                   r"(?:hasn.t|has not|haven.t) (?:arrived|come|been delivered|shown up)|"
                   r"still (?:not here|hasn.t|waiting)|not here|delivery date passed|"
                   r"supposed to (?:come|arrive)", re.I)
_QUESTION_START = re.compile(r"^\s*(?:is|are|was|what|when|has|have|do|does|did|can you tell|could you tell|"
                             r"where is order|where's order)\b", re.I)


def _intent(message: str) -> str:
    if _REQUEST.search(message):
        return "escalate"
    is_question = bool(_QUESTION_START.search(message)) and message.strip().endswith("?")
    if _LATE.search(message):
        return "information" if is_question else "escalate"
    return "information" if is_question else "unclear"


def wants_escalation(customer_messages: list[str]) -> bool:
    """The latest message decides if it is clear; otherwise earlier turns do (e.g. "It's order 1050.")."""
    for message in reversed(customer_messages):
        intent = _intent(message)
        if intent != "unclear":
            return intent == "escalate"
    return False
