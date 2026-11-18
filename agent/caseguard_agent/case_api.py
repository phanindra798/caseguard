"""HTTP client for the Case API: the agent's only way to read or change CRM data.

- Identity (tenant, customer) comes from the trusted SESSION and is sent as headers on every call.
  The LLM never supplies it.
- Errors are mapped from the API's stable `code` field, never from message text.
- A timeout, connection error or 5xx raises `Unavailable`: the outcome is UNKNOWN, not "failed",
  because the server may have done the work before the response was lost.
- Every call is appended to `trace` (ToolCall records) for the harness and for debugging.
"""
from __future__ import annotations

from typing import Any

import httpx

from .models import ToolCall, dump


class CaseApiError(Exception):
    def __init__(self, code: str, status: int, body: dict | None = None):
        super().__init__(f"{status} {code}")
        self.code, self.status, self.body = code, status, body or {}


class NotFound(CaseApiError):
    """Missing OR not the caller's. The API deliberately doesn't say which."""


class AlreadyOpen(CaseApiError):
    @property
    def existing_escalation_id(self) -> str:
        return str(self.body.get("existingEscalationId"))


class CaseNotOpen(CaseApiError):
    pass


class OperationConflict(CaseApiError):
    """Same Idempotency-Key used for a different request. Always a bug on our side."""


class Unavailable(Exception):
    """Timeout / connection error / 5xx. The request MAY have been executed."""


_ERRORS = {"not_found": NotFound, "already_open": AlreadyOpen, "case_not_open": CaseNotOpen,
           "operation_conflict": OperationConflict}


class CaseApiClient:
    def __init__(self, base_url: str, tenant_id: str, customer_id: str, *,
                 transport: httpx.BaseTransport | None = None, timeout_s: float = 3.0):
        self.trace: list[dict] = []
        self._http = httpx.Client(
            base_url=base_url, transport=transport, timeout=timeout_s,
            headers={"X-Tenant-Id": tenant_id, "X-Customer-Id": customer_id})

    def close(self) -> None:
        self._http.close()

    # ---------------------------------------------------------------- core
    def _call(self, tool: str, method: str, path: str, *, operation_id: str | None = None,
              **kwargs: Any) -> Any:
        headers = {"Idempotency-Key": operation_id} if operation_id else None
        try:
            resp = self._http.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException:
            self._record(tool, method, path, None, "timeout", operation_id)
            raise Unavailable(f"{tool}: timeout")
        except httpx.TransportError as e:
            self._record(tool, method, path, None, "unavailable", operation_id)
            raise Unavailable(f"{tool}: {e}")

        if resp.status_code >= 500:
            self._record(tool, method, path, resp.status_code, "unavailable", operation_id)
            raise Unavailable(f"{tool}: HTTP {resp.status_code}")
        body = resp.json() if resp.content else None
        if resp.status_code >= 400:
            code = (body or {}).get("code", f"http_{resp.status_code}")
            self._record(tool, method, path, resp.status_code, code, operation_id)
            raise _ERRORS.get(code, CaseApiError)(code, resp.status_code, body)
        self._record(tool, method, path, resp.status_code, "ok", operation_id)
        return body

    def _record(self, tool, method, path, status, outcome, operation_id):
        self.trace.append(dump(ToolCall(tool=tool, method=method, path=path, status=status,
                                        outcome=outcome, operation_id=operation_id)))

    # ---------------------------------------------------------------- reads
    def list_orders(self) -> list[dict]:
        return self._call("list_orders", "GET", "/orders")

    def get_order(self, order_id: str) -> dict:
        return self._call("get_order", "GET", f"/orders/{order_id}")

    def get_cases(self, order_id: str) -> list[dict]:
        return self._call("get_cases", "GET", "/cases", params={"orderId": order_id})

    def get_escalation(self, escalation_id: str) -> dict:
        return self._call("get_escalation_status", "GET", f"/escalations/{escalation_id}")

    def find_escalation_by_operation(self, operation_id: str) -> dict | None:
        found = self._call("find_escalation_by_operation", "GET", "/escalations",
                           params={"operationId": operation_id})
        return found[0] if found else None

    # ---------------------------------------------------------------- write
    def request_escalation(self, case_id: str, operation_id: str, reason: str, policy_ref: str) -> dict:
        return self._call("request_escalation", "POST", f"/cases/{case_id}/escalations",
                          operation_id=operation_id, json={"reason": reason, "policyRef": policy_ref})
