"""A real language model, through LangChain's provider-neutral `init_chat_model`.

Set CASEGUARD_MODEL to "<provider>:<model-name>" and install LangChain's integration package for
that provider. No provider is hardcoded in this project.

Both calls use STRUCTURED OUTPUT (the model must return JSON matching our Pydantic schema), so the
graph never has to parse free text.
"""
from __future__ import annotations

import json

from ..models import PlanStep, ReplyDraft
from ..usage import UsageMeter
from .prompts import PLANNER_PROMPT, REPLY_PROMPT


class LiveLLM:
    def __init__(self, model: str, meter: UsageMeter | None = None):
        from langchain.chat_models import init_chat_model

        chat = init_chat_model(model, temperature=0)
        # include_raw=True: keep the raw message too, because it carries the token counts.
        self._planner = chat.with_structured_output(PlanStep, include_raw=True)
        self._writer = chat.with_structured_output(ReplyDraft, include_raw=True)
        self.meter = meter or UsageMeter()

    def _call(self, runnable, system: str, view: dict):
        out = runnable.invoke([("system", system), ("user", json.dumps(view, default=str))])
        usage = getattr(out.get("raw"), "usage_metadata", None) or {}
        self.meter.record(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))
        if out.get("parsed") is None:
            raise ValueError(f"model output did not match the schema: {out.get('parsing_error')}")
        return out["parsed"]

    def plan(self, view: dict) -> PlanStep:
        return self._call(self._planner, PLANNER_PROMPT, view)

    def write_reply(self, view: dict) -> ReplyDraft:
        return self._call(self._writer, REPLY_PROMPT, view)
