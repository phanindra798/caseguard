"""HTTP and terminal entry points for the agent.

    uvicorn caseguard_agent.app:app --port 8090
        POST /chat                         customer message
        GET  /conversations/{id}           messages + whether it waits for an approval
        GET  /supervisor?as=sup-maria      supervisor page: pending approvals, Approve / Reject
        POST /supervisor/decide            decide → the paused conversation resumes
    python -m caseguard_agent.app          chat in the terminal (/approve, /reject act as the supervisor)

Environment:
    CASE_API_URL             default http://localhost:8081
    CASEGUARD_LLM            scripted (default) | live
    CASEGUARD_MODEL          for live mode: "<provider>:<model-name>" (LangChain init_chat_model format)
    CASEGUARD_CHECKPOINT_DB  postgresql://... to keep conversations across restarts (default: memory)
"""
from __future__ import annotations

import html
import os
import uuid
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .case_api import CaseApiClient, CaseApiError, SupervisorClient
from .checkpoints import make_checkpointer
from .graph import AgentDeps, build_graph
from .llm import make_llm
from .policies import load_policies
from .runner import AgentRunner, Session

CASE_API_URL = os.getenv("CASE_API_URL", "http://localhost:8081")

# Demo identities. In production a login token maps to these; the chat text never does.
SESSIONS = {
    "demo-asha": Session(tenant_id="acme", customer_id="cust-A1", region="US"),
    "demo-ben": Session(tenant_id="acme", customer_id="cust-A2", region="EU"),
    "demo-chen": Session(tenant_id="globex", customer_id="cust-G1", region="US"),
}
SUPERVISORS = {"sup-maria": "acme", "sup-gary": "globex"}     # supervisor id -> their tenant


def make_runner() -> AgentRunner:
    deps = AgentDeps(
        llm=make_llm(os.getenv("CASEGUARD_LLM", "scripted"), os.getenv("CASEGUARD_MODEL")),
        case_api=lambda tenant, customer: CaseApiClient(CASE_API_URL, tenant, customer),
        policies=load_policies())
    return AgentRunner(build_graph(deps, checkpointer=make_checkpointer()))


app = FastAPI(title="CaseGuard Agent")
_runner: AgentRunner | None = None


def runner() -> AgentRunner:
    global _runner
    _runner = _runner or make_runner()
    return _runner


def decide_and_resume(supervisor_id: str, approval_id: str, decision: str) -> dict:
    """The supervisor decides in the Case API (the source of truth), then the paused conversation
    resumes. The agent re-reads the approval itself; the resume signal is only a wake-up call."""
    tenant = SUPERVISORS[supervisor_id]
    sup = SupervisorClient(CASE_API_URL, tenant, supervisor_id)
    try:
        approval = sup.decide(approval_id, decision)
    finally:
        sup.close()
    conversation = approval.get("conversationRef")
    if conversation and runner().waiting_for_approval(conversation) == approval_id:
        return {"approval": approval, "resumed": runner().resume(conversation, {"approval_id": approval_id})}
    return {"approval": approval, "resumed": None}


# ------------------------------------------------------------------ customer API
class ChatRequest(BaseModel):
    session: str
    message: str
    conversation_id: str | None = None


@app.post("/chat")
def chat(req: ChatRequest) -> dict:
    session = SESSIONS.get(req.session)
    if session is None:
        raise HTTPException(status_code=401, detail="unknown session")
    conversation_id = req.conversation_id or str(uuid.uuid4())
    return {"conversation_id": conversation_id, **runner().turn(conversation_id, session, req.message)}


@app.get("/conversations/{conversation_id}")
def conversation(conversation_id: str) -> dict:
    return {"conversation_id": conversation_id, "messages": runner().messages(conversation_id),
            "waiting_for_approval": runner().waiting_for_approval(conversation_id)}


# ------------------------------------------------------------------ supervisor page
@app.get("/supervisor", response_class=HTMLResponse)
def supervisor_page(request: Request) -> str:
    who = request.query_params.get("as", "sup-maria")
    if who not in SUPERVISORS:
        raise HTTPException(status_code=401, detail="unknown supervisor")
    sup = SupervisorClient(CASE_API_URL, SUPERVISORS[who], who)
    try:
        pending = sup.pending()
    finally:
        sup.close()
    rows = "".join(
        f"<tr><td>{html.escape(a['caseId'])}</td><td>{html.escape(a['reason'])}</td>"
        f"<td>{html.escape(a['policyRef'])}</td><td>v{a['caseVersion']}</td>"
        f"<td><form method='post' action='/supervisor/decide'>"
        f"<input type='hidden' name='as' value='{html.escape(who)}'>"
        f"<input type='hidden' name='approval_id' value='{html.escape(a['id'])}'>"
        f"<button name='decision' value='APPROVED'>Approve</button> "
        f"<button name='decision' value='REJECTED'>Reject</button></form></td></tr>" for a in pending)
    return _page(f"Pending approvals for {html.escape(SUPERVISORS[who])} ({html.escape(who)})",
                 f"<table><tr><th>Case</th><th>Reason</th><th>Policy</th><th>Case version</th><th></th></tr>"
                 f"{rows or '<tr><td colspan=5>Nothing waiting.</td></tr>'}</table>")


@app.post("/supervisor/decide", response_class=HTMLResponse)
async def supervisor_decide(request: Request) -> str:
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    who, approval_id, decision = form.get("as"), form.get("approval_id"), form.get("decision")
    if who not in SUPERVISORS or decision not in ("APPROVED", "REJECTED") or not approval_id:
        raise HTTPException(status_code=400, detail="bad decision")
    try:
        result = decide_and_resume(who, approval_id, decision)
    except CaseApiError as e:
        return _page("Could not record the decision", f"<p>{html.escape(e.code)}</p>")
    reply = (result["resumed"] or {}).get("reply", {}).get("text", "(conversation not running in this agent)")
    return _page(f"{decision.title()}",
                 f"<p>The customer's conversation resumed. The agent said:</p><blockquote>{html.escape(reply)}"
                 f"</blockquote><p><a href='/supervisor?as={html.escape(who)}'>Back</a></p>")


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>CaseGuard supervisor</title>"
            "<style>body{font-family:system-ui;margin:2rem;max-width:60rem}td,th{padding:.4rem .8rem;"
            "border-bottom:1px solid #ddd;text-align:left}blockquote{background:#f4f4f4;padding:1rem}</style>"
            f"</head><body><h1>{title}</h1>{body}</body></html>")


# ------------------------------------------------------------------ terminal
def main() -> None:
    r = runner()
    session = SESSIONS[os.getenv("CASEGUARD_SESSION", "demo-asha")]
    conversation_id = str(uuid.uuid4())
    print(f"Chatting as {session.customer_id} ({session.tenant_id}). "
          "/approve or /reject act as supervisor sup-maria. Ctrl+C to quit.\n")
    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not message:
            continue
        if message in ("/approve", "/reject"):
            waiting = r.waiting_for_approval(conversation_id)
            if not waiting:
                print("   (nothing is waiting for approval)\n")
                continue
            decision = "APPROVED" if message == "/approve" else "REJECTED"
            print(f"   [supervisor sup-maria] {decision} approval {waiting}")
            result = decide_and_resume("sup-maria", waiting, decision)["resumed"]
        else:
            result = r.turn(conversation_id, session, message)
        for d in result["decisions"]:
            print(f"   [plan] {d['action']}{' ' + d['tool'] if d.get('tool') else ''}: {d['reason']}")
        for t in result["tool_calls"]:
            print(f"   [tool] {t['method']} {t['path']} -> {t['status']} {t['outcome']}")
        print(f"agent> {result['reply']['text']}")
        print(f"   [claims] {[c['type'] for c in result['reply']['claims']]}"
              f"{'   [paused: waiting for approval]' if result.get('waiting_for_approval') else ''}\n")


if __name__ == "__main__":
    main()
