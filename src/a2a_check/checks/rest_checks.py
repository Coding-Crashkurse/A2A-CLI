from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple

from ..http_client import HttpClient
from ..config import Settings
from ..models import CheckResult, Section, Severity

class RestChecks:
    """
    HTTP+JSON mapping checks.

    We assume the REST base URL is declared in the AgentCard either as preferredTransport
    or within additionalInterfaces (transport == "HTTP+JSON"). All endpoints are relative
    to that base.
    """

    def __init__(self, http: HttpClient, settings: Settings, rest_base_url: str) -> None:
        self.http = http
        self.settings = settings
        self.base = rest_base_url.rstrip("/")

    def _send_payload(self) -> Dict[str, Any]:
        return {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "ping"}],
                "messageId": "rest-msg-1"
            },
            "configuration": {"blocking": False}
        }

    def run_section(self) -> Section:
        s = Section(title="HTTP+JSON")

        # message:send
        try:
            resp = self.http.post_json(f"{self.base}/message:send", self._send_payload())
            ct_ok = "application/json" in resp.headers.get("content-type", "").lower()
            s.extend([
                CheckResult(rule="REST-010", ok=resp.status_code in (200, 202), message=f"message:send HTTP {resp.status_code}", severity=Severity.ERROR if resp.status_code >= 400 else Severity.INFO),
                CheckResult(rule="REST-011", ok=ct_ok, message="Content-Type application/json" if ct_ok else "Content-Type not JSON", severity=Severity.ERROR if not ct_ok else Severity.INFO),
            ])
            dl = resp.json() if ct_ok else {}
        except Exception as e:
            s.extend([CheckResult(rule="REST-010", ok=False, message=f"message:send transport error {e}", severity=Severity.ERROR)])
            return s

        # Extract Task ID if returned
        rest_tid: Optional[str] = None
        if isinstance(dl, dict):
            if dl.get("kind") == "task" and isinstance(dl.get("id"), str):
                rest_tid = dl["id"]

        # message:stream (SSE)
        try:
            payload = {"message": {"role": "user", "parts": [{"kind": "text", "text": "stream test"}], "messageId": "rest-stream-1"}}
            with self.http.sse_post(f"{self.base}/message:stream", payload) as es:
                events = list(es.iter_sse())
            ok_stream = len(events) > 0
            s.extend([CheckResult(rule="REST-020", ok=ok_stream, message="received SSE events" if ok_stream else "no SSE events", severity=Severity.ERROR if not ok_stream else Severity.INFO)])
        except Exception as e:
            s.extend([CheckResult(rule="REST-020", ok=False, message=f"message:stream error {e}", severity=Severity.ERROR)])

        # tasks GET & cancel if we got an id
        if rest_tid:
            try:
                r = self.http.get(f"{self.base}/tasks/{rest_tid}")
                ct_ok = "application/json" in r.headers.get("content-type", "").lower()
                s.extend([
                    CheckResult(rule="REST-030", ok=r.status_code == 200, message=f"GET tasks/{rest_tid} HTTP {r.status_code}", severity=Severity.ERROR if r.status_code != 200 else Severity.INFO),
                    CheckResult(rule="REST-031", ok=ct_ok, message="Content-Type application/json" if ct_ok else "Content-Type not JSON", severity=Severity.ERROR if not ct_ok else Severity.INFO),
                ])
            except Exception as e:
                s.extend([CheckResult(rule="REST-030", ok=False, message=f"tasks GET transport error {e}", severity=Severity.ERROR)])

            try:
                r = self.http.post_json(f"{self.base}/tasks/{rest_tid}:cancel", {})
                s.extend([CheckResult(rule="REST-032", ok=r.status_code in (200, 202), message=f"POST tasks/{rest_tid}:cancel HTTP {r.status_code}", severity=Severity.ERROR if r.status_code >= 400 else Severity.INFO)])
            except Exception as e:
                s.extend([CheckResult(rule="REST-032", ok=False, message=f"tasks cancel transport error {e}", severity=Severity.ERROR)])
        else:
            s.extend([CheckResult(rule="REST-030", ok=True, message="tasks GET/Cancel skipped (no task id from send)", severity=Severity.INFO)])
        return s
