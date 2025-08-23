from __future__ import annotations
import json
import time
import uuid
from typing import Dict, Any, Generator, List

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

app = FastAPI()

# Globaler Schalter – wird in main(...) gesetzt
WRONG_MODE: bool = False

# sehr simple In-Memory-Tasks
TASKS: Dict[str, Dict[str, Any]] = {}


def _good_agent_card(base: str) -> Dict[str, Any]:
    return {
        "protocolVersion": "0.3.0",
        "name": "Hello World Agent",
        "description": "Minimal A2A-compliant dummy agent (JSON-RPC + SSE).",
        "url": f"{base}/a2a/v1",
        "preferredTransport": "JSONRPC",
        "additionalInterfaces": [
            {"url": f"{base}/a2a/v1", "transport": "JSONRPC"}
        ],
        "version": "1.0.0",
        "capabilities": {"streaming": True, "pushNotifications": False, "stateTransitionHistory": False},
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": [
            {"id": "hello_world", "name": "Hello World", "description": "Returns hello world", "tags": ["hello", "world"]}
        ],
        "supportsAuthenticatedExtendedCard": False
    }


def _bad_agent_card(base: str) -> Dict[str, Any]:
    """
    Liefert absichtlich fehlerhafte Felder, damit die Test-Suite rote Einträge zeigt:
    - 'name' fehlt (ERROR)
    - 'defaultInputModes' fehlt (ERROR)
    - duplicate skill ids + fehlende description (ERROR)
    - preferredTransport=JSONRPC, aber additionalInterfaces enthält NUR GRPC (CARD-011 error)
    - capabilities.streaming ist string statt bool (ERROR)
    """
    return {
        "protocolVersion": "0.3.0",
        # "name": fehlt absichtlich
        "description": "This card is intentionally broken for testing.",
        "url": f"{base}/a2a/v1",
        "preferredTransport": "JSONRPC",
        "additionalInterfaces": [
            {"url": f"{base}/a2a/v1", "transport": "GRPC"}  # JSONRPC fehlt absichtlich -> Mismatch
        ],
        "version": "1.0.0",
        "capabilities": {"streaming": "yes", "pushNotifications": False},  # falscher Typ
        # "defaultInputModes": fehlt absichtlich
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": [
            {"id": "hello_world", "name": "Hello World", "tags": ["hello", "world"]},  # description fehlt
            {"id": "hello_world", "name": "Hello World Again", "description": "dup id", "tags": ["x"]},  # duplicate id
        ],
        "supportsAuthenticatedExtendedCard": False
    }


def agent_card(base: str) -> Dict[str, Any]:
    return _bad_agent_card(base) if WRONG_MODE else _good_agent_card(base)


@app.get("/.well-known/agent-card.json")
def get_card(request: Request):
    base = str(request.base_url).rstrip("/")
    # absichtlich immer JSON zurückgeben (Network+Schema sollen laufen),
    # CardChecks zeigen dann die gezielten Fehler
    return JSONResponse(agent_card(base))


def sse_from_events(events: List[Dict[str, Any]]):
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
        # Standard: korrekter Content-Type application/json
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})

    def ok_text_plain(result: Any):
        # Falscher Content-Type für RPC-011 Fehler
        data = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})
        return Response(content=data, media_type="text/plain")

    def err(code: int, message: str):
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}, status_code=200)

    allowed = {
        "message/send", "message/stream", "tasks/get", "tasks/cancel", "tasks/resubscribe",
        "agent/getAuthenticatedExtendedCard",
        # Push-Notifs absichtlich nicht implementiert -> -32601 in den Checks (toleriert als INFO)
    }
    if method not in allowed:
        return err(-32601, "Method not found")

    if method == "agent/getAuthenticatedExtendedCard":
        # No auth in dummy -> pretend secured endpoint
        return Response(status_code=401)

    if method == "message/send":
        msg = params.get("message") or {}
        text_parts = [p for p in msg.get("parts", []) if p.get("kind") == "text"]
        if not text_parts:
            return err(-32602, "Invalid params: missing text part")

        # Erzeuge einen Task
        tid = str(uuid.uuid4())
        TASKS[tid] = {
            "id": tid,
            "contextId": str(uuid.uuid4()),
            "status": {"state": "submitted", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ")},
            "history": [msg],
            "kind": "task",
            "metadata": {}
        }

        # In WRONG_MODE: falscher Content-Type -> RPC-011 ❌
        return ok_text_plain(TASKS[tid]) if WRONG_MODE else ok(TASKS[tid])

    if method == "tasks/get":
        tid = (params.get("id") or "")
        if tid not in TASKS:
            return err(-32001, "Task not found")
        return ok(TASKS[tid])

    if method == "tasks/cancel":
        tid = (params.get("id") or "")
        if tid not in TASKS:
            return err(-32001, "Task not found")
        # Für Demo: nicht cancelbar -> -32002 (INFO in den Checks)
        return err(-32002, "Task cannot be canceled")

    if method == "message/stream":
        msg = params.get("message") or {}
        tid = str(uuid.uuid4())
        ctx = str(uuid.uuid4())
        TASKS[tid] = {"id": tid, "contextId": ctx, "status": {"state": "submitted"}, "history": [msg], "kind": "task"}

        events = [
            {"jsonrpc": "2.0", "id": req_id, "result": TASKS[tid]},
            {"jsonrpc": "2.0", "id": req_id, "result": {"taskId": tid, "contextId": ctx, "kind": "status-update", "status": {"state": "working"}, "final": False}},
            {"jsonrpc": "2.0", "id": req_id, "result": {"taskId": tid, "contextId": ctx, "kind": "status-update", "status": {"state": "completed"}, "final": True}},
        ]
        return EventSourceResponse(sse_from_events(events)())

    if method == "tasks/resubscribe":
        tid = (params.get("id") or "")
        if tid not in TASKS:
            # Resubscribe auf unbekannten Task -> 404 (führt zu RPC-032 ❌, wenn du es provozieren willst)
            return Response(status_code=404)
        ctx = TASKS[tid].get("contextId") or str(uuid.uuid4())
        events = [
            {"jsonrpc": "2.0", "id": req_id, "result": {"taskId": tid, "contextId": ctx, "kind": "status-update", "status": {"state": "completed"}, "final": True}},
        ]
        return EventSourceResponse(sse_from_events(events)())

    return err(-32601, "Method not found")


def main(host: str = "127.0.0.1", port: int = 9999, wrong: bool = False):
    """
    Startet den Dummy-Server.
    :param wrong: Wenn True, liefert der Server absichtlich Card-/RPC-Verstöße,
                  damit die a2a-check Suite Fehler anzeigt.
    """
    import uvicorn
    global WRONG_MODE
    WRONG_MODE = bool(wrong)
    uvicorn.run(app, host=host, port=port)
