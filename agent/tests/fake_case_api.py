"""An in-memory Case API that behaves like the real one, plus switches to make it misbehave.

Same seed data, ownership rules, idempotency and error codes as the Java service, so the agent
can be tested fast and deterministically. The REAL service is tested by tests/test_e2e.py.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import httpx


class FakeCaseApi:
    def __init__(self, today: date):
        d = lambda days: (today - timedelta(days=days)).isoformat()
        self.orders = {
            "1042": dict(id="1042", tenant="acme", customer="cust-A1", description="Blue rain jacket",
                         amountCents=12000, status="SHIPPED", promisedDate=d(10), deliveredAt=None),
            "1043": dict(id="1043", tenant="acme", customer="cust-A1", description="Wool socks (3 pack)",
                         amountCents=2500, status="SHIPPED", promisedDate=d(2), deliveredAt=None),
            "1050": dict(id="1050", tenant="acme", customer="cust-A1", description="Trail running shoes",
                         amountCents=14000, status="SHIPPED", promisedDate=d(12), deliveredAt=None),
            "1099": dict(id="1099", tenant="acme", customer="cust-A1", description="Camping tent (4 person)",
                         amountCents=75000, status="SHIPPED", promisedDate=d(12), deliveredAt=None),
            "2001": dict(id="2001", tenant="globex", customer="cust-G1", description="Kayak paddle",
                         amountCents=18000, status="SHIPPED", promisedDate=d(15), deliveredAt=None),
        }
        self.cases = {f"C-{o}": dict(id=f"C-{o}", orderId=o, tenant=v["tenant"], customer=v["customer"],
                                     subject="late", status="OPEN", version=0)
                      for o, v in self.orders.items()}
        self.escalations: dict[str, dict] = {}
        self.approvals: dict[str, dict] = {}
        self.approval_over_cents = 50000
        # --- misbehaviour switches ---
        self.worker = "instant"          # "instant": COMPLETED on first status read; "down": stays REQUESTED
        self.timeout_after_write = 0     # next N successful POSTs: server acts, then the response is lost
        self.unavailable_no_write = 0    # next N POSTs: 503 without writing
        self.lookup_unavailable = False  # GET /escalations?operationId= returns 503
        self.posts: list[dict] = []      # every POST attempt (for assertions)

    # ------------------------------------------------------------------ helpers
    def escalations_for(self, case_id: str) -> list[dict]:
        return [e for e in self.escalations.values() if e["caseId"] == case_id]

    @staticmethod
    def _problem(status: int, code: str, **extra) -> httpx.Response:
        return httpx.Response(status, json={"status": status, "code": code, **extra})

    def _owned(self, item: dict | None, tenant: str, customer: str) -> bool:
        return item is not None and item["tenant"] == tenant and item["customer"] == customer

    @staticmethod
    def _public(item: dict) -> dict:
        return {k: v for k, v in item.items() if k not in ("tenant", "customer")}

    # ------------------------------------------------------------------ transport
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        tenant, customer = req.headers.get("X-Tenant-Id"), req.headers.get("X-Customer-Id")
        if not tenant or not customer:
            return self._problem(400, "missing_identity")
        path, method = req.url.path, req.method

        if method == "GET" and path == "/orders":
            return httpx.Response(200, json=[self._public(o) for o in self.orders.values()
                                             if self._owned(o, tenant, customer)])
        if method == "GET" and path.startswith("/orders/"):
            o = self.orders.get(path.split("/")[2])
            return httpx.Response(200, json=self._public(o)) if self._owned(o, tenant, customer) \
                else self._problem(404, "not_found")
        if method == "GET" and path.startswith("/cases/") and path.count("/") == 2:
            c = self.cases.get(path.split("/")[2])
            return httpx.Response(200, json=self._public(c)) if self._owned(c, tenant, customer) \
                else self._problem(404, "not_found")
        if method == "POST" and path.endswith("/approvals"):
            return self._post_approval(req, path.split("/")[2], tenant, customer)
        if method == "GET" and path.startswith("/approvals/"):
            a = self.approvals.get(path.split("/")[2])
            return httpx.Response(200, json=self._public(a)) if self._owned(a, tenant, customer) \
                else self._problem(404, "not_found")
        if method == "GET" and path == "/cases":
            oid = req.url.params.get("orderId")
            return httpx.Response(200, json=[self._public(c) for c in self.cases.values()
                                             if c["orderId"] == oid and self._owned(c, tenant, customer)])
        if method == "POST" and path.endswith("/escalations"):
            return self._post_escalation(req, path.split("/")[2], tenant, customer)
        if method == "GET" and path == "/escalations":
            if self.lookup_unavailable:
                return httpx.Response(503)
            op = req.url.params.get("operationId")
            return httpx.Response(200, json=[self._public(e) for e in self.escalations.values()
                                             if e["operationId"] == op and self._owned(e, tenant, customer)])
        if method == "GET" and path.startswith("/escalations/"):
            e = self.escalations.get(path.split("/")[2])
            if not self._owned(e, tenant, customer):
                return self._problem(404, "not_found")
            if self.worker == "instant" and e["status"] == "REQUESTED":
                e["status"] = "COMPLETED"
            return httpx.Response(200, json=self._public(e))
        return httpx.Response(404)

    def _post_escalation(self, req, case_id, tenant, customer) -> httpx.Response:
        op = req.headers.get("Idempotency-Key")
        self.posts.append({"case_id": case_id, "operation_id": op})
        if self.unavailable_no_write > 0:
            self.unavailable_no_write -= 1
            return httpx.Response(503)
        case = self.cases.get(case_id)
        if not self._owned(case, tenant, customer):
            return self._problem(404, "not_found")
        same_op = next((e for e in self.escalations.values() if e["operationId"] == op), None)
        if same_op:
            if same_op["caseId"] != case_id or not self._owned(same_op, tenant, customer):
                return self._problem(409, "operation_conflict")
            return self._maybe_lose(req, httpx.Response(200, json=self._public(same_op),
                                                        headers={"Idempotent-Replayed": "true"}))
        if case["status"] != "OPEN":
            return self._problem(409, "case_not_open")
        open_ = next((e for e in self.escalations_for(case_id) if e["status"] != "FAILED"), None)
        if open_:
            return self._problem(409, "already_open", existingEscalationId=open_["id"])
        body = json.loads(req.content)
        if self.orders[case["orderId"]]["amountCents"] > self.approval_over_cents:
            why = self._approval_problem(body.get("approvalId"), case, op, body["policyRef"], tenant, customer)
            if why:
                return self._problem(403, "approval_required", why=why)
        e = dict(id=str(uuid.uuid4()), caseId=case_id, operationId=op, status="REQUESTED",
                 reason=body["reason"], policyRef=body["policyRef"], tenant=tenant, customer=customer)
        self.escalations[e["id"]] = e
        return self._maybe_lose(req, httpx.Response(202, json=self._public(e)))

    def _maybe_lose(self, req, resp: httpx.Response) -> httpx.Response:
        """The server DID the work (or the replay), but the response never reaches the client."""
        if self.timeout_after_write > 0:
            self.timeout_after_write -= 1
            raise httpx.ReadTimeout("response lost after the server acted", request=req)
        return resp

    # ------------------------------------------------------------------ approvals
    def _post_approval(self, req, case_id, tenant, customer) -> httpx.Response:
        op = req.headers.get("Idempotency-Key")
        case = self.cases.get(case_id)
        if not self._owned(case, tenant, customer):
            return self._problem(404, "not_found")
        same = next((a for a in self.approvals.values() if a["operationId"] == op), None)
        if same:
            if same["caseId"] != case_id or not self._owned(same, tenant, customer):
                return self._problem(409, "operation_conflict")
            return httpx.Response(200, json=self._public(same))
        body = json.loads(req.content)
        if body["caseVersion"] != case["version"]:
            return self._problem(409, "case_changed")
        a = dict(id=str(uuid.uuid4()), caseId=case_id, operationId=op, caseVersion=case["version"],
                 policyRef=body["policyRef"], reason=body["reason"], conversationRef=body.get("conversationRef"),
                 status="PENDING", expiresAt="2999-01-01T00:00:00+00:00", tenant=tenant, customer=customer)
        self.approvals[a["id"]] = a
        return httpx.Response(201, json=self._public(a))

    def _approval_problem(self, approval_id, case, op, policy_ref, tenant, customer) -> str | None:
        """Same binding rules as the Java ApprovalService.requireValidApproval."""
        if not approval_id:
            return "missing"
        a = self.approvals.get(approval_id)
        if not self._owned(a, tenant, customer):
            return "not_found"
        if a["caseId"] != case["id"]:
            return "wrong_case"
        if a["operationId"] != op:
            return "wrong_operation"
        if a["status"] != "APPROVED":
            return a["status"].lower()
        if a["caseVersion"] != case["version"]:
            return "case_changed"
        if a["policyRef"] != policy_ref:
            return "policy_changed"
        return None

    # helpers for tests (what a supervisor / another employee would do)
    def decide(self, approval_id: str, decision: str) -> None:
        self.approvals[approval_id]["status"] = decision

    def latest_approval(self) -> dict:
        return list(self.approvals.values())[-1]

    def edit_case(self, case_id: str) -> None:
        self.cases[case_id]["version"] += 1
