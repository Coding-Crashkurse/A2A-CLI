from __future__ import annotations
import uuid
import time
from typing import Any, Dict, Tuple, Optional
from pydantic import ValidationError
from a2a.types import (
    SendMessageRequest,
    SendStreamingMessageRequest,
    JSONRPCResponse,
    JSONRPCErrorResponse,
    Message,
    TextPart,
    MessageSendParams,
    MessageSendConfiguration,
)
from .http_client import HttpClient
from .config import Settings


class JsonRpcClient:
    """JSON-RPC 2.0 client targeting A2A endpoints."""

    def __init__(self, http: HttpClient, settings: Settings, url: str) -> None:
        self.http = http
        self.settings = settings
        self.url = url

    def send_text(self, text: str, blocking: bool | None = None, context: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        msg = Message(
            role="user",
            parts=[TextPart(text=text)],
            message_id=str(uuid.uuid4()),
        )
        cfg = MessageSendConfiguration()
        if blocking is not None:
            cfg.blocking = blocking
        params = MessageSendParams(message=msg, configuration=cfg)
        req = SendMessageRequest(id=str(uuid.uuid4()), params=params)
        payload = req.model_dump(mode="json", exclude_none=True)
        resp = self.http.post_json(self.url, payload)
        return payload, {"status_code": resp.status_code, "headers": dict(resp.headers), "text": resp.text}

    def call_and_parse(self, payload: Dict[str, Any]) -> Tuple[JSONRPCResponse | JSONRPCErrorResponse, Dict[str, Any]]:
        resp = self.http.post_json(self.url, payload)
        raw = {"status_code": resp.status_code, "headers": dict(resp.headers), "text": resp.text}
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError("Response is not JSON")
        try:
            parsed = JSONRPCResponse.model_validate(data).root
            return parsed, raw
        except ValidationError:
            try:
                err = JSONRPCErrorResponse.model_validate(data)
                return err, raw
            except ValidationError as ve:
                raise RuntimeError(f"Invalid JSON-RPC payload: {ve}")

    def stream_text(self, text: str) -> Tuple[Dict[str, Any], list[Dict[str, Any]]]:
        msg = Message(
            role="user",
            parts=[TextPart(text=text)],
            message_id=str(uuid.uuid4()),
        )
        params = MessageSendParams(message=msg)
        req = SendStreamingMessageRequest(id=str(uuid.uuid4()), params=params)
        payload = req.model_dump(mode="json", exclude_none=True)
        events: list[Dict[str, Any]] = []
        deadline = time.monotonic() + self.settings.stream_timeout_s
        with self.http.sse_post(self.url, payload) as es:
            for sse in es.iter_sse():
                events.append({"event": sse.event, "data": sse.data})
                if time.monotonic() > deadline:
                    break
        return payload, events
