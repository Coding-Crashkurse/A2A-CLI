from __future__ import annotations
from typing import Dict, Any, Optional

from ..http_client import HttpClient
from ..config import Settings
from ..models import CheckResult, Section, Severity


class RestChecks:
    """
    HTTP+JSON mapping checks (direct to the agent, no proxy).

    We accept a REST *base* that can be either:
      - the service root (e.g. http://localhost:8003), or
      - the versioned base (e.g. http://localhost:8003/v1)

    Endpoints are resolved as:
        <base>/v1/<endpoint>    if base does not already end with /v1
        <base>/<endpoint>       if base already ends with /v1
    """

    def __init__(self, http: HttpClient, settings: Settings, rest_base_url: str) -> None:
        self.http = http
        self.settings = settings
        self.base = rest_base_url.rstrip("/")

    # ------- helpers -------

    def _ep(self, suffix: str) -> str:
        """Resolve endpoint path for suffix like 'message:send' or 'tasks/{id}'."""
        b = self.base
        s = suffix.lstrip("/")
        if b.endswith("/v1"):
            return f"{b}/{s}"
        return f"{b}/v1/{s}"

    def _send_payload(self) -> Dict[str, Any]:
        return {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "ping"}],
                "messageId": "rest-msg-1"
            },
            "configuration": {"blocking": False}
        }

    # ------- checks -------

    def run_section(self) -> Section:
        s = Section(title="HTTP+JSON")

        # message:send
        try:
            resp = self.http.post_json(self._ep("message:send"), self._send_payload())
            ct_ok = "application/json" in resp.headers.get("content-type", "").lower()
            s.extend([
                CheckResult(
                    rule="REST-010",
                    ok=resp.status_code in (200, 202),
                    message=f"POST message:send HTTP {resp.status_code}",
                    severity=Severity.ERROR if resp.status_code >= 400 else Severity.INFO,
                ),
                CheckResult(
                    rule="REST-011",
                    ok=ct_ok,
                    message="Content-Type application/json" if ct_ok else "Content-Type not JSON",
                    severity=Severity.ERROR if not ct_ok else Severity.INFO,
                ),
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
            payload = {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "stream test"}],
                    "messageId": "rest-stream-1",
                }
            }
            with self.http.sse_post(self._ep("message:stream"), payload) as es:
                events = list(es.iter_sse())
            ok_stream = len(events) > 0
            s.extend([
                CheckResult(
                    rule="REST-020",
                    ok=ok_stream,
                    message="received SSE events" if ok_stream else "no SSE events",
                    severity=Severity.ERROR if not ok_stream else Severity.INFO,
                )
            ])
        except Exception as e:
            s.extend([CheckResult(rule="REST-020", ok=False, message=f"message:stream error {e}", severity=Severity.ERROR)])

        # tasks GET / cancel / subscribe (if we got an id)
        if rest_tid:
            # GET
            try:
                r = self.http.get(self._ep(f"tasks/{rest_tid}"))
                ct_ok = "application/json" in r.headers.get("content-type", "").lower()
                s.extend([
                    CheckResult(
                        rule="REST-030",
                        ok=r.status_code == 200,
                        message=f"GET tasks/{rest_tid} HTTP {r.status_code}",
                        severity=Severity.ERROR if r.status_code != 200 else Severity.INFO,
                    ),
                    CheckResult(
                        rule="REST-031",
                        ok=ct_ok,
                        message="Content-Type application/json" if ct_ok else "Content-Type not JSON",
                        severity=Severity.ERROR if not ct_ok else Severity.INFO,
                    ),
                ])
            except Exception as e:
                s.extend([CheckResult(rule="REST-030", ok=False, message=f"tasks GET transport error {e}", severity=Severity.ERROR)])

            # CANCEL
            try:
                r = self.http.post_json(self._ep(f"tasks/{rest_tid}:cancel"), {})
                s.extend([
                    CheckResult(
                        rule="REST-032",
                        ok=r.status_code in (200, 202),
                        message=f"POST tasks/{rest_tid}:cancel HTTP {r.status_code}",
                        severity=Severity.ERROR if r.status_code >= 400 else Severity.INFO,
                    )
                ])
            except Exception as e:
                s.extend([CheckResult(rule="REST-032", ok=False, message=f"tasks cancel transport error {e}", severity=Severity.ERROR)])

            # SUBSCRIBE (SSE resubscribe)
            try:
                with self.http.sse_post(self._ep(f"tasks/{rest_tid}:subscribe"), {}) as es:
                    revents = list(es.iter_sse())
                ok_sub = len(revents) > 0
                s.extend([
                    CheckResult(
                        rule="REST-033",
                        ok=ok_sub,
                        message="tasks:subscribe yielded events" if ok_sub else "tasks:subscribe yielded no events",
                        severity=Severity.ERROR if not ok_sub else Severity.INFO,
                    )
                ])
            except Exception as e:
                s.extend([CheckResult(rule="REST-033", ok=False, message=f"tasks:subscribe error {e}", severity=Severity.ERROR)])
        else:
            s.extend([CheckResult(rule="REST-030", ok=True, message="tasks GET/Cancel/Subscribe skipped (no task id from send)", severity=Severity.INFO)])

        # optional: authenticated extended card via REST GET /v1/card
        try:
            r = self.http.get(self._ep("card"))
            if r.status_code in (401, 403):
                s.extend([
                    CheckResult(
                        rule="REST-050",
                        ok=True,
                        message=f"GET /v1/card HTTP {r.status_code} (expected without auth)",
                        severity=Severity.INFO,
                    )
                ])
            elif r.status_code == 200:
                ct_ok = "application/json" in r.headers.get("content-type", "").lower()
                s.extend([
                    CheckResult(
                        rule="REST-050",
                        ok=True,
                        message="GET /v1/card OK",
                        severity=Severity.INFO,
                    ),
                    CheckResult(
                        rule="REST-051",
                        ok=ct_ok,
                        message="Content-Type application/json" if ct_ok else "Content-Type not JSON",
                        severity=Severity.ERROR if not ct_ok else Severity.INFO,
                    ),
                ])
            else:
                s.extend([
                    CheckResult(
                        rule="REST-050",
                        ok=False,
                        message=f"GET /v1/card HTTP {r.status_code}",
                        severity=Severity.ERROR,
                    )
                ])
        except Exception as e:
            s.extend([CheckResult(rule="REST-050", ok=False, message=f"/v1/card transport error {e}", severity=Severity.ERROR)])

        return s
