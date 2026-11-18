"""LLM layer. The graph only depends on the AgentLLM protocol, so the brain is swappable."""
from .base import AgentLLM
from .scripted import ScriptedLLM

__all__ = ["AgentLLM", "ScriptedLLM", "make_llm"]


def make_llm(mode: str, model: str | None = None) -> AgentLLM:
    """mode 'scripted' = deterministic rules (tests, CI); 'live' = a real model via LangChain."""
    if mode == "scripted":
        return ScriptedLLM()
    if mode == "live":
        from .live import LiveLLM   # imported lazily: live mode needs the optional `langchain` extra
        if not model:
            raise ValueError("live mode needs CASEGUARD_MODEL, e.g. '<provider>:<model-name>'")
        return LiveLLM(model)
    raise ValueError(f"unknown LLM mode: {mode}")
