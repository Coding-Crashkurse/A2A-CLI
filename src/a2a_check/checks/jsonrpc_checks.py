from __future__ import annotations
import json
from typing import List, Dict, Any, Optional, Tuple
from a2a.types import (
    AgentCard,
    JSONRPCErrorResponse,
    JSONRPCResponse,
    SendMessageRequest,
    Message,
    TextPart,
    MessageSendParams,
    MessageSendConfiguration,
    GetTaskRequest,
    TaskQueryParams,
    CancelTaskRequest,
    TaskIdParams,
)
from ..jsonrpc_client import JsonRpcClient
from ..models import CheckResult, Section, Severity


class JsonRpcChecks:
    """Runs JSON-RPC compliance checks against an A2A server."""

    def __init__(self, card: AgentCard, jsonrpc_url: str, client: JsonRpcClient) -> None:
        self.card = card
        self.jsonrpc_url = jsonrpc_url
        self.client = client

    def run_section(self) -> Section:
        s = Section(title="JSON-RPC")
        s.extend(self._method_not_found())
        ping_results, task_id = self._message_send_roundtrip()
        s.extend(ping_results)
        if task_id:
            s.extend(self._tasks_get(task_id))
            s.extend(self._tasks_cancel(task_id))
        s.extend(self._streaming())
        return s

    def _select_text(self) -> str:
        return "ping"

    def _method_not_found(self) -> List[CheckResult]:
        payload = {"jsonrpc": "2.0", "id": "test", "method": "foo/bar"}
        try:
            parsed, raw = self.client.call_and_parse(payload)
            if isinstance(parsed, JSONRPCErrorResponse):
                code = parsed.error.code
                ok = code == -32601
                return [CheckResult(rule="RPC-001", ok=ok, message="method not found error" if ok else f"unexpected error code {code}", severity=Severity.ERROR if not ok else Severity.INFO)]
            else:
                return [CheckResult(rule="RPC-001", ok=False, message="expected error for unknown method", severity=Severity.ERROR)]
        except Exception as e:
            return [CheckResult(rule="RPC-001", ok=False, message=f"transport error {e}", severity=Severity.ERROR)]

    def _message_send_roundtrip(self) -> Tuple[List[CheckResult], Optional[str]]:
        text = self._select_text()
        msg = Message(role="user", parts=[TextPart(text=text)], message_id="msg-1")
        cfg = MessageSendConfiguration(blocking=False)
        params = MessageSendParams(message=msg, configuration=cfg)
        req = SendMessageRequest(id="1", params=params)
        try:
            parsed, raw = self.client.call_and_parse(req.model_dump(mode="json", exclude_none=True))
        except Exception as e:
            return [CheckResult(rule="RPC-010", ok=False, message=f"message/send failed {e}", severity=Severity.ERROR)], None
        hdr_ok = "application/json" in (raw["headers"].get("content-type", "").lower())
        r_hdr = CheckResult(rule="RPC-011", ok=hdr_ok, message="Content-Type application/json" if hdr_ok else "Content-Type not JSON", severity=Severity.ERROR if not hdr_ok else Severity.INFO)
        if isinstance(parsed, JSONRPCErrorResponse):
            return [CheckResult(rule="RPC-010", ok=False, message=f"message/send error {parsed.error.code} {parsed.error.message}", severity=Severity.ERROR), r_hdr], None
        result = parsed.result
        if isinstance(result, dict) and result.get("kind") == "message":
            return [CheckResult(rule="RPC-010", ok=True, message="message/send returned Message", severity=Severity.INFO), r_hdr], None
        if isinstance(result, dict) and result.get("kind") == "task":
            tid = result.get("id")
            ok = isinstance(tid, str) and len(tid) > 0
            return [CheckResult(rule="RPC-010", ok=True, message="message/send returned Task", severity=Severity.INFO), r_hdr], tid if ok else None
        return [CheckResult(rule="RPC-010", ok=False, message="unexpected result shape", severity=Severity.ERROR), r_hdr], None

    def _tasks_get(self, task_id: str) -> List[CheckResult]:
        req = GetTaskRequest(id="2", params=TaskQueryParams(id=task_id, history_length=1))
        try:
            parsed, _ = self.client.call_and_parse(req.model_dump(mode="json", exclude_none=True))
        except Exception as e:
            return [CheckResult(rule="RPC-020", ok=False, message=f"tasks/get transport error {e}", severity=Severity.ERROR)]
        if isinstance(parsed, JSONRPCErrorResponse):
            return [CheckResult(rule="RPC-020", ok=False, message=f"tasks/get error {parsed.error.code} {parsed.error.message}", severity=Severity.ERROR)]
        result = parsed.result
        ok = isinstance(result, dict) and result.get("kind") == "task" and result.get("id") == task_id
        return [CheckResult(rule="RPC-020", ok=ok, message="tasks/get returned Task" if ok else "tasks/get unexpected result", severity=Severity.ERROR if not ok else Severity.INFO)]

    def _tasks_cancel(self, task_id: str) -> List[CheckResult]:
        req = CancelTaskRequest(id="3", params=TaskIdParams(id=task_id))
        try:
            parsed, _ = self.client.call_and_parse(req.model_dump(mode="json", exclude_none=True))
        except Exception as e:
            return [CheckResult(rule="RPC-021", ok=False, message=f"tasks/cancel transport error {e}", severity=Severity.ERROR)]
        if isinstance(parsed, JSONRPCErrorResponse):
            code = parsed.error.code
            if code in (-32002,):
                return [CheckResult(rule="RPC-021", ok=True, message="task not cancelable", severity=Severity.INFO)]
            return [CheckResult(rule="RPC-021", ok=False, message=f"tasks/cancel error {code} {parsed.error.message}", severity=Severity.ERROR)]
        result = parsed.result
        ok = isinstance(result, dict) and result.get("kind") == "task"
        return [CheckResult(rule="RPC-021", ok=ok, message="tasks/cancel returned Task" if ok else "tasks/cancel unexpected result", severity=Severity.ERROR if not ok else Severity.INFO)]

    def _streaming(self) -> List[CheckResult]:
        caps = self.card.capabilities or {}
        streaming = getattr(caps, "streaming", None)
        if streaming is False:
            return [CheckResult(rule="RPC-030", ok=True, message="streaming not declared; skipping stream test", severity=Severity.INFO)]
        try:
            payload, events = self.client.stream_text("stream test")
        except Exception as e:
            return [CheckResult(rule="RPC-030", ok=False, message=f"message/stream failed {e}", severity=Severity.ERROR)]
        ok_any = len(events) > 0
        return [CheckResult(rule="RPC-030", ok=ok_any, message="received SSE events" if ok_any else "no SSE events received", severity=Severity.ERROR if not ok_any else Severity.INFO)]
