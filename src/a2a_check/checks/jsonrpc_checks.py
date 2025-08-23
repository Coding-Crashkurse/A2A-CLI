from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple, Union
import json

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


def _kind_and_id(obj: Any) -> Tuple[Optional[str], Optional[str]]:
    """Extract 'kind' and 'id' from either a Pydantic model or a plain dict."""
    if obj is None:
        return None, None

    # Pydantic v2 Models
    kind = getattr(obj, "kind", None)
    _id = getattr(obj, "id", None)
    if kind is not None or _id is not None:
        return kind, _id

    # As dict
    if isinstance(obj, dict):
        return obj.get("kind"), obj.get("id")

    # Fallback: try model_dump
    try:
        if hasattr(obj, "model_dump"):
            d = obj.model_dump()  # type: ignore[attr-defined]
            return d.get("kind"), d.get("id")
    except Exception:
        pass

    return None, None


def _try_parse_json(s: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(s)
    except Exception:
        return None


def _extract_task_id_from_stream_events(events: List[Dict[str, Any]]) -> Optional[str]:
    """
    Each SSE data is a JSON-RPC 2.0 Response object.
    We scan for result types that include a task id (Task or status/artifact update).
    """
    for e in events:
        d = _try_parse_json(e.get("data", "")) or {}
        if not isinstance(d, dict):
            continue
        res = d.get("result")
        if isinstance(res, dict):
            # Task result
            if res.get("kind") == "task" and isinstance(res.get("id"), str):
                return res["id"]
            # Events carry taskId field
            if isinstance(res.get("taskId"), str):
                return res["taskId"]
            # In some implementations initial result may be a Message with no task id; continue
    return None


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
            s.extend(self._push_notifications(task_id))

        s.extend(self._streaming_and_resubscribe())
        s.extend(self._extended_card())
        return s

    def _select_text(self) -> str:
        return "ping"

    def _method_not_found(self) -> List[CheckResult]:
        """
        Unknown method should yield a JSON-RPC error.
        Accept these codes:
          -32601 Method not found
          -32600 Invalid request
          -32602 Invalid params
        """
        payload = {"jsonrpc": "2.0", "id": "test", "method": "foo/bar"}
        try:
            parsed, _ = self.client.call_and_parse(payload)
            if isinstance(parsed, JSONRPCErrorResponse):
                code = parsed.error.code
                ok = code in (-32601, -32600, -32602)
                msg = (
                    f"unknown method rejected with acceptable JSON-RPC error ({code})"
                    if ok else f"unexpected error code {code}"
                )
                return [CheckResult(rule="RPC-001", ok=ok, message=msg, severity=Severity.ERROR if not ok else Severity.INFO)]
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

        # Content-Type check
        hdr_ok = "application/json" in (raw["headers"].get("content-type", "").lower())
        r_hdr = CheckResult(rule="RPC-011", ok=hdr_ok, message="Content-Type application/json" if hdr_ok else "Content-Type not JSON", severity=Severity.ERROR if not hdr_ok else Severity.INFO)

        if isinstance(parsed, JSONRPCErrorResponse):
            return [
                CheckResult(rule="RPC-010", ok=False, message=f"message/send error {parsed.error.code} {parsed.error.message}", severity=Severity.ERROR),
                r_hdr,
            ], None

        # Success path
        try:
            result = parsed.result  # type: ignore[attr-defined]
        except Exception:
            return [
                CheckResult(rule="RPC-010", ok=False, message=f"unexpected success type {type(parsed).__name__}", severity=Severity.ERROR),
                r_hdr,
            ], None

        kind, tid = _kind_and_id(result)
        if kind == "message":
            return [CheckResult(rule="RPC-010", ok=True, message="message/send returned Message", severity=Severity.INFO), r_hdr], None
        if kind == "task":
            ok = isinstance(tid, str) and len(tid) > 0
            return [
                CheckResult(rule="RPC-010", ok=True, message="message/send returned Task", severity=Severity.INFO),
                r_hdr,
            ], tid if ok else None

        type_info = getattr(result, "__class__", type(result)).__name__
        return [
            CheckResult(rule="RPC-010", ok=False, message=f"unexpected result shape (type={type_info}, kind={kind})", severity=Severity.ERROR),
            r_hdr,
        ], None

    def _tasks_get(self, task_id: str) -> List[CheckResult]:
        req = GetTaskRequest(id="2", params=TaskQueryParams(id=task_id, history_length=1))
        try:
            parsed, _ = self.client.call_and_parse(req.model_dump(mode="json", exclude_none=True))
        except Exception as e:
            return [CheckResult(rule="RPC-020", ok=False, message=f"tasks/get transport error {e}", severity=Severity.ERROR)]

        if isinstance(parsed, JSONRPCErrorResponse):
            return [CheckResult(rule="RPC-020", ok=False, message=f"tasks/get error {parsed.error.code} {parsed.error.message}", severity=Severity.ERROR)]

        result = parsed.result  # type: ignore[attr-defined]
        kind, rid = _kind_and_id(result)
        ok = (kind == "task") and (rid == task_id)
        return [CheckResult(rule="RPC-020", ok=ok, message="tasks/get returned Task" if ok else f"tasks/get unexpected result (kind={kind}, id={rid})", severity=Severity.ERROR if not ok else Severity.INFO)]

    def _tasks_cancel(self, task_id: str) -> List[CheckResult]:
        req = CancelTaskRequest(id="3", params=TaskIdParams(id=task_id))
        try:
            parsed, _ = self.client.call_and_parse(req.model_dump(mode="json", exclude_none=True))
        except Exception as e:
            return [CheckResult(rule="RPC-021", ok=False, message=f"tasks/cancel transport error {e}", severity=Severity.ERROR)]

        if isinstance(parsed, JSONRPCErrorResponse):
            code = parsed.error.code
            if code in (-32002,):  # TaskNotCancelableError
                return [CheckResult(rule="RPC-021", ok=True, message="task not cancelable", severity=Severity.INFO)]
            return [CheckResult(rule="RPC-021", ok=False, message=f"tasks/cancel error {code} {parsed.error.message}", severity=Severity.ERROR)]

        result = parsed.result  # type: ignore[attr-defined]
        kind, _ = _kind_and_id(result)
        ok = (kind == "task")
        return [CheckResult(rule="RPC-021", ok=ok, message="tasks/cancel returned Task" if ok else f"tasks/cancel unexpected result (kind={kind})", severity=Severity.ERROR if not ok else Severity.INFO)]

    def _streaming_and_resubscribe(self) -> List[CheckResult]:
        caps = getattr(self.card, "capabilities", None) or {}
        streaming = getattr(caps, "streaming", None)
        if streaming is False:
            return [CheckResult(rule="RPC-030", ok=True, message="streaming not declared; skipping stream test", severity=Severity.INFO)]
        try:
            payload, events = self.client.stream_text("stream test")
        except Exception as e:
            return [CheckResult(rule="RPC-030", ok=False, message=f"message/stream failed {e}", severity=Severity.ERROR)]

        ok_any = len(events) > 0
        results = [CheckResult(rule="RPC-030", ok=ok_any, message="received SSE events" if ok_any else "no SSE events received", severity=Severity.ERROR if not ok_any else Severity.INFO)]
        if not ok_any:
            return results

        tid = _extract_task_id_from_stream_events(events)
        if not tid:
            results.append(CheckResult(rule="RPC-031", ok=False, message="could not extract taskId from SSE events; skipping resubscribe", severity=Severity.WARN))
            return results

        # Try resubscribe
        try:
            _, revents = self.client.resubscribe(task_id=tid)
            ok_re = len(revents) > 0
            results.append(CheckResult(rule="RPC-032", ok=ok_re, message="tasks/resubscribe yielded events" if ok_re else "tasks/resubscribe yielded no events", severity=Severity.ERROR if not ok_re else Severity.INFO))
        except Exception as e:
            results.append(CheckResult(rule="RPC-032", ok=False, message=f"tasks/resubscribe failed {e}", severity=Severity.ERROR))
        return results

    def _push_notifications(self, task_id: str) -> List[CheckResult]:
        # Optional nach Spec: Agenten dürfen Push nicht unterstützen
        set_payload = {
            "jsonrpc": "2.0",
            "id": "push-1",
            "method": "tasks/pushNotificationConfig/set",
            "params": {
                "taskId": task_id,
                "pushNotificationConfig": {
                    "url": "https://client.example.com/webhook/a2a-notifications",
                    "token": "dummy-check-token",
                    "authentication": {"schemes": ["Bearer"]}
                }
            }
        }
        results: List[CheckResult] = []
        try:
            parsed, _ = self.client.call_and_parse(set_payload)
        except Exception as e:
            return [CheckResult(rule="RPC-040", ok=False, message=f"pushNotification set transport error {e}", severity=Severity.ERROR)]

        # Tolerant: -32003 (not supported) ODER -32601 (method not found)
        if isinstance(parsed, JSONRPCErrorResponse):
            if parsed.error.code in (-32003, -32601):
                results.append(CheckResult(
                    rule="RPC-040",
                    ok=True,
                    message=f"push notifications not supported ({parsed.error.code})",
                    severity=Severity.INFO
                ))
                return results
            else:
                results.append(CheckResult(
                    rule="RPC-040",
                    ok=False,
                    message=f"unexpected push set error {parsed.error.code} {parsed.error.message}",
                    severity=Severity.ERROR
                ))
                return results

        # Success path: versuchen, ID zu greifen
        res = getattr(parsed, "result", None)
        pn_id: Optional[str] = None
        if isinstance(res, dict):
            cfg = res.get("pushNotificationConfig") or {}
            pn_id = cfg.get("id")

        results.append(CheckResult(rule="RPC-040", ok=True, message="pushNotificationConfig set OK", severity=Severity.INFO))

        # GET (optional, falls id vorhanden)
        get_payload = {
            "jsonrpc": "2.0",
            "id": "push-2",
            "method": "tasks/pushNotificationConfig/get",
            "params": {"id": task_id} | ({"pushNotificationConfigId": pn_id} if pn_id else {})
        }
        try:
            gparsed, _ = self.client.call_and_parse(get_payload)
            if isinstance(gparsed, JSONRPCErrorResponse):
                # Auch hier tolerant, falls Server in Folge-Calls -32601/-32003 liefert
                if gparsed.error.code in (-32003, -32601):
                    results.append(CheckResult(rule="RPC-041", ok=True, message=f"push get not supported ({gparsed.error.code})", severity=Severity.INFO))
                else:
                    results.append(CheckResult(rule="RPC-041", ok=False, message=f"push get error {gparsed.error.code} {gparsed.error.message}", severity=Severity.ERROR))
            else:
                results.append(CheckResult(rule="RPC-041", ok=True, message="pushNotificationConfig get OK", severity=Severity.INFO))
        except Exception as e:
            results.append(CheckResult(rule="RPC-041", ok=False, message=f"push get transport error {e}", severity=Severity.ERROR))

        # LIST
        list_payload = {
            "jsonrpc": "2.0",
            "id": "push-3",
            "method": "tasks/pushNotificationConfig/list",
            "params": {"id": task_id}
        }
        try:
            lparsed, _ = self.client.call_and_parse(list_payload)
            if isinstance(lparsed, JSONRPCErrorResponse):
                if lparsed.error.code in (-32003, -32601):
                    results.append(CheckResult(rule="RPC-042", ok=True, message=f"push list not supported ({lparsed.error.code})", severity=Severity.INFO))
                else:
                    results.append(CheckResult(rule="RPC-042", ok=False, message=f"push list error {lparsed.error.code} {lparsed.error.message}", severity=Severity.ERROR))
            else:
                results.append(CheckResult(rule="RPC-042", ok=True, message="pushNotificationConfig list OK", severity=Severity.INFO))
        except Exception as e:
            results.append(CheckResult(rule="RPC-042", ok=False, message=f"push list transport error {e}", severity=Severity.ERROR))

        # DELETE (nur wenn id vorhanden)
        if pn_id:
            del_payload = {
                "jsonrpc": "2.0",
                "id": "push-4",
                "method": "tasks/pushNotificationConfig/delete",
                "params": {"id": task_id, "pushNotificationConfigId": pn_id}
            }
            try:
                dparsed, _ = self.client.call_and_parse(del_payload)
                if isinstance(dparsed, JSONRPCErrorResponse):
                    if dparsed.error.code in (-32003, -32601):
                        results.append(CheckResult(rule="RPC-043", ok=True, message=f"push delete not supported ({dparsed.error.code})", severity=Severity.INFO))
                    else:
                        results.append(CheckResult(rule="RPC-043", ok=False, message=f"push delete error {dparsed.error.code} {dparsed.error.message}", severity=Severity.ERROR))
                else:
                    results.append(CheckResult(rule="RPC-043", ok=True, message="pushNotificationConfig delete OK", severity=Severity.INFO))
            except Exception as e:
                results.append(CheckResult(rule="RPC-043", ok=False, message=f"push delete transport error {e}", severity=Severity.ERROR))
        else:
            results.append(CheckResult(rule="RPC-043", ok=True, message="push delete skipped (no id returned by server)", severity=Severity.INFO))

        return results

    def _extended_card(self) -> List[CheckResult]:
        saec = getattr(self.card, "supports_authenticated_extended_card", None)
        # In Pydantic models fields may be camelCase; try both
        if saec is None:
            saec = getattr(self.card, "supportsAuthenticatedExtendedCard", None)

        if not saec:
            return [CheckResult(rule="RPC-050", ok=True, message="extended card not declared; skipping", severity=Severity.INFO)]

        # Build payload
        payload = {"jsonrpc": "2.0", "id": "ac-1", "method": "agent/getAuthenticatedExtendedCard"}
        try:
            resp = self.client.call_raw(payload)
        except Exception as e:
            return [CheckResult(rule="RPC-050", ok=False, message=f"extended card transport error {e}", severity=Severity.ERROR)]

        # No token -> often 401/403. With token -> 200 and AgentCard.
        sc = resp.get("status_code", 0)
        if sc in (401, 403):
            return [CheckResult(rule="RPC-050", ok=True, message=f"extended card returned HTTP {sc} (expected without auth)", severity=Severity.INFO)]

        # Try parse JSON
        try:
            data = json.loads(resp["text"])
        except Exception:
            return [CheckResult(rule="RPC-050", ok=False, message="extended card response not JSON", severity=Severity.ERROR)]

        # Could still be JSON-RPC error with 200
        try:
            parsed = JSONRPCResponse.model_validate(data).root
        except Exception:
            try:
                err = JSONRPCErrorResponse.model_validate(data)
                return [CheckResult(rule="RPC-050", ok=False, message=f"extended card JSON-RPC error {err.error.code} {err.error.message}", severity=Severity.ERROR)]
            except Exception:
                return [CheckResult(rule="RPC-050", ok=False, message="extended card invalid JSON-RPC payload", severity=Severity.ERROR)]

        if isinstance(parsed, JSONRPCErrorResponse):
            return [CheckResult(rule="RPC-050", ok=False, message=f"extended card error {parsed.error.code} {parsed.error.message}", severity=Severity.ERROR)]

        # Validate AgentCard shape in result
        res = getattr(parsed, "result", None)
        if not isinstance(res, dict):
            return [CheckResult(rule="RPC-050", ok=False, message="extended card result not an object", severity=Severity.ERROR)]

        # Try Pydantic validation
        try:
            _ = AgentCard.model_validate(res)
            return [CheckResult(rule="RPC-050", ok=True, message="extended card OK (validated AgentCard)", severity=Severity.INFO)]
        except Exception as ve:
            return [CheckResult(rule="RPC-050", ok=False, message=f"extended card invalid AgentCard: {ve}", severity=Severity.ERROR)]
