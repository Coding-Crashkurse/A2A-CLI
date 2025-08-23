from __future__ import annotations
import json
import time
import uuid
from typing import Dict, Any, Generator, List, Literal

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

app = FastAPI()

MODE: Literal["ok", "errors", "mixed", "warn"] = "ok"

TASKS: Dict[str, Dict[str, Any]] = {}


def _ok_card(base: str) -> Dict[str, Any]:
    return {
        "protocolVersion": "0.3.0",
        "name": "Hello World Agent",
        "description": "Minimal A2A-compliant dummy agent (JSON-RPC + SSE).",
        "url": f"{base}/a2a/v1",
        "preferredTransport": "JSONRPC",
        "additionalInterfaces": [{"url": f"{base}/a2a/v1", "transport": "JSONRPC"}],
        "version": "1.0.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": [
            {
                "id": "hello_world",
                "name": "Hello World",
                "description": "Returns hello world",
                "tags": ["hello", "world"],
            }
        ],
        "supportsAuthenticatedExtendedCard": False,
    }


def _errors_card(base: str) -> Dict[str, Any]:
    return {
        "protocolVersion": "0.3.0",
        "description": "Broken card for ERROR demo.",
        "url": f"{base}/a2a/v1",
        "preferredTransport": "JSONRPC",
        "additionalInterfaces": [
            {"url": f"{base}/a2a/v1", "transport": "GRPC"}
        ],
        "version": "1.0.0",
        "capabilities": {
            "streaming": "yes",
            "pushNotifications": False,
        },
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": [
            {
                "id": "hello_world",
                "name": "Hello World",
                "tags": ["hello", "world"],
            },
            {
                "id": "hello_world",
                "name": "Hello World Again",
                "description": "dup id",
                "tags": ["x"],
            },
        ],
        "supportsAuthenticatedExtendedCard": False,
    }


def _mixed_card(base: str) -> Dict[str, Any]:
    return {
        "protocolVersion": "0.2.9",
        "name": "Hello World Agent (mixed)",
        "description": "Card that mixes warnings and errors for demo.",
        "url": f"{base}/a2a/v1",
        "preferredTransport": "JSONRPC",
        "additionalInterfaces": [
            {"url": f"{base}/a2a/v1", "transport": "JSONRPC"},
            {"url": f"{base}/rest", "transport": "HTTP_JSON"},
        ],
        "iconUrl": "ftp://example.com/icon.png",
        "version": "1.0",
        "capabilities": {
            "streaming": "yes",
            "pushNotifications": False,
            "stateTransitionHistory": False,
            "extensions": [{"description": "missing uri"}],
        },
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": [
            {"id": "hello_world", "name": "Hello World", "tags": ["hello", "world"]},
            {"id": "hello_world", "name": "Hello World Again", "description": "dup", "tags": ["x"]},
        ],
        "supportsAuthenticatedExtendedCard": True,
    }


def _warn_card(base: str) -> Dict[str, Any]:
    return {
        "protocolVersion": "0.2.9",
        "name": "Hello World Agent (warn-only)",
        "description": "Card that triggers warnings without schema errors.",
        "url": f"{base}/a2a/v1",
        "preferredTransport": "JSONRPC",
        "additionalInterfaces": [{"url": f"{base}/a2a/v1", "transport": "JSONRPC"}],
        "iconUrl": "ftp://example.com/icon.png",
        "version": "1.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": [
            {
                "id": "hello_world",
                "name": "Hello World",
                "description": "Returns hello world",
                "tags": ["hello", "world"],
            }
        ],
        "supportsAuthenticatedExtendedCard": True,
    }


def _card_for_mode(base: str) -> Dict[str, Any]:
    if MODE == "ok":
        return _ok_card(base)
    if MODE == "errors":
        return _errors_card(base)
    if MODE == "warn":
        return _warn_card(base)
    return _mixed_card(base)


@app.get("/.well-known/agent-card.json")
def get_card(request: Request):
    base = str(request.base_url).rstrip("/")
    return JSONResponse(_card_for_mode(base))


def _sse(events: List[Dict[str, Any]]):
    def gen() -> Generator[Dict[str, str], None, None]:
        for ev in events:
            yield {"event": "message", "data": json.dumps(ev)}
            time.sleep(0.05)
    return gen


@app.post("/a2a/v1")
async def jsonrpc(request: Request):
    payload = await request.json()
    method = payload.get("method")
    req_id = payload.get("id")
    params = payload.get("params") or {}

    def ok(result: Any):
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})

    def ok_text_plain(result: Any):
        data = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})
        return Response(content=data, media_type="text/plain")

    def err(code: int, message: str):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
            status_code=200,
        )

    allowed = {
        "message/send",
        "message/stream",
        "tasks/get",
        "tasks/cancel",
        "tasks/resubscribe",
        "agent/getAuthenticatedExtendedCard",
    }
    if method not in allowed:
        return err(-32601, "Method not found")

    if method == "agent/getAuthenticatedExtendedCard":
        return Response(status_code=401)

    if method == "message/send":
        msg = params.get("message") or {}
        text_parts = [p for p in msg.get("parts", []) if p.get("kind") == "text"]
        if not text_parts:
            return err(-32602, "Invalid params: missing text part")
        tid = str(uuid.uuid4())
        TASKS[tid] = {
            "id": tid,
            "contextId": str(uuid.uuid4()),
            "status": {"state": "submitted", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ")},
            "history": [msg],
            "kind": "task",
            "metadata": {},
        }
        if MODE in ("errors", "mixed"):
            return ok_text_plain(TASKS[tid])
        return ok(TASKS[tid])

    if method == "tasks/get":
        tid = params.get("id") or ""
        if tid not in TASKS:
            return err(-32001, "Task not found")
        return ok(TASKS[tid])

    if method == "tasks/cancel":
        tid = params.get("id") or ""
        if tid not in TASKS:
            return err(-32001, "Task not found")
        return err(-32002, "Task cannot be canceled")

    if method == "message/stream":
        msg = params.get("message") or {}
        tid = str(uuid.uuid4())
        ctx = str(uuid.uuid4())
        TASKS[tid] = {
            "id": tid,
            "contextId": ctx,
            "status": {"state": "submitted"},
            "history": [msg],
            "kind": "task",
        }
        events = [
            {"jsonrpc": "2.0", "id": req_id, "result": TASKS[tid]},
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "taskId": tid,
                    "contextId": ctx,
                    "kind": "status-update",
                    "status": {"state": "working"},
                    "final": False,
                },
            },
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "taskId": tid,
                    "contextId": ctx,
                    "kind": "status-update",
                    "status": {"state": "completed"},
                    "final": True,
                },
            },
        ]
        return EventSourceResponse(_sse(events)())

    if method == "tasks/resubscribe":
        tid = params.get("id") or ""
        if tid not in TASKS:
            return Response(status_code=404)
        ctx = TASKS[tid].get("contextId") or str(uuid.uuid4())
        events = [
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "taskId": tid,
                    "contextId": ctx,
                    "kind": "status-update",
                    "status": {"state": "completed"},
                    "final": True,
                },
            },
        ]
        return EventSourceResponse(_sse(events)())

    return err(-32601, "Method not found")


def main(host: str = "127.0.0.1", port: int = 9999, mode: str = "ok"):
    import uvicorn
    global MODE
    mode_norm = (mode or "ok").strip().lower()
    if mode_norm not in ("ok", "errors", "mixed", "warn"):
        mode_norm = "ok"
    MODE = mode_norm
    uvicorn.run(app, host=host, port=port)
