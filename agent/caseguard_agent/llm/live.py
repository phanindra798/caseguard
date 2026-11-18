"""A real language model, through LangChain's provider-neutral `init_chat_model`.

Set CASEGUARD_MODEL to "<provider>:<model-name>" and install LangChain's integration package for
that provider. No provider is hardcoded in this project.

Both calls use STRUCTURED OUTPUT (the model must return JSON matching our Pydantic schema), so the
graph never has to parse free text.
"""
from __future__ import annotations

import json

from ..models import PlanStep, ReplyDraft
from .prompts import PLANNER_PROMPT, REPLY_PROMPT


class LiveLLM:
    def __init__(self, model: str):
        from langchain.chat_models import init_chat_model

        chat = init_chat_model(model, temperature=0)
        self._planner = chat.with_structured_output(PlanStep)
        self._writer = chat.with_structured_output(ReplyDraft)

    def plan(self, view: dict) -> PlanStep:
        return self._planner.invoke([("system", PLANNER_PROMPT), ("user", json.dumps(view, default=str))])

    def write_reply(self, view: dict) -> ReplyDraft:
        return self._writer.invoke([("system", REPLY_PROMPT), ("user", json.dumps(view, default=str))])
