"""HTTP and terminal entry points for the agent.

    uvicorn caseguard_agent.app:app --port 8090        # POST /chat
    python -m caseguard_agent.app                       # chat in the terminal

Environment:
    CASE_API_URL     default http://localhost:8081
    CASEGUARD_LLM    scripted (default) | live
    CASEGUARD_MODEL  for live mode: "<provider>:<model-name>" (LangChain init_chat_model format)
"""
from __future__ import annotations

import os
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .case_api import CaseApiClient
from .graph import AgentDeps, build_graph
from .llm import make_llm
from .policies import load_policies
from .runner import AgentRunner, Session

# Demo sessions. In production a login token would map to the identity; the chat text never does.
SESSIONS = {
    "demo-asha": Session(tenant_id="acme", customer_id="cust-A1", region="US"),
    "demo-ben": Session(tenant_id="acme", customer_id="cust-A2", region="EU"),
    "demo-chen": Session(tenant_id="globex", customer_id="cust-G1", region="US"),
}


def make_runner() -> AgentRunner:
    base_url = os.getenv("CASE_API_URL", "http://localhost:8081")
    deps = AgentDeps(
        llm=make_llm(os.getenv("CASEGUARD_LLM", "scripted"), os.getenv("CASEGUARD_MODEL")),
        case_api=lambda tenant, customer: CaseApiClient(base_url, tenant, customer),
        policies=load_policies())
    return AgentRunner(build_graph(deps))


app = FastAPI(title="CaseGuard Agent")
_runner: AgentRunner | None = None


class ChatRequest(BaseModel):
    session: str
    message: str
    conversation_id: str | None = None


@app.post("/chat")
def chat(req: ChatRequest) -> dict:
    global _runner
    session = SESSIONS.get(req.session)
    if session is None:
        raise HTTPException(status_code=401, detail="unknown session")
    _runner = _runner or make_runner()
    conversation_id = req.conversation_id or str(uuid.uuid4())
    result = _runner.turn(conversation_id, session, req.message)
    return {"conversation_id": conversation_id, **result}


def main() -> None:
    runner = make_runner()
    session = SESSIONS[os.getenv("CASEGUARD_SESSION", "demo-asha")]
    conversation_id = str(uuid.uuid4())
    print(f"Chatting as {session.customer_id} ({session.tenant_id}). Ctrl+C to quit.\n")
    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not message:
            continue
        r = runner.turn(conversation_id, session, message)
        for d in r["decisions"]:
            print(f"   [plan] {d['action']}{' ' + d['tool'] if d.get('tool') else ''}: {d['reason']}")
        for t in r["tool_calls"]:
            print(f"   [tool] {t['method']} {t['path']} -> {t['status']} {t['outcome']}")
        print(f"agent> {r['reply']['text']}")
        print(f"   [claims] {[c['type'] for c in r['reply']['claims']]}\n")


if __name__ == "__main__":
    main()
