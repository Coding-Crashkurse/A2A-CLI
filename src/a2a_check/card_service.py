# src/a2a_check/card_service.py
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from pydantic import ValidationError
from a2a.types import AgentCard
from .config import Settings
from .http_client import HttpClient
from .models import CheckResult, Severity
from .util import resolve_card_url, build_origin


class CardService:
    """Fetches and validates AgentCards."""

    def __init__(self, http: HttpClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings

    def fetch_raw(
        self, target: str, override_card_url: str | None
    ) -> Tuple[str, Dict[str, Any], List[CheckResult]]:
        results: List[CheckResult] = []

        origin = build_origin(target)
        card_url = resolve_card_url(target, override_card_url, self.settings.well_known_path)

        # Probe the origin. Any HTTP response counts as "reachable".
        try:
            resp_origin = self.http.get(origin)
            results.append(
                CheckResult(
                    rule="NET-001",
                    ok=True,
                    message=f"Origin reachable HTTP {resp_origin.status_code}",
                    severity=Severity.INFO,
                )
            )
        except Exception as e:
            results.append(
                CheckResult(
                    rule="NET-001",
                    ok=False,
                    message=f"Origin not reachable: {e}",
                    severity=Severity.ERROR,
                )
            )

        raw: Dict[str, Any] = {}

        # Probe the AgentCard endpoint regardless of the origin result.
        try:
            resp = self.http.get(card_url)
            results.append(
                CheckResult(
                    rule="URL-001",
                    ok=True,
                    message=f"Card endpoint reachable HTTP {resp.status_code}",
                    severity=Severity.INFO,
                )
            )

            if resp.status_code == 200:
                results.append(CheckResult(rule="HTTP-200", ok=True, message="HTTP 200 OK", severity=Severity.INFO))
            else:
                results.append(CheckResult(rule="HTTP-200", ok=False, message=f"Unexpected HTTP status {resp.status_code}", severity=Severity.ERROR))

            ctype = (resp.headers.get("content-type") or "").lower()
            if "application/json" in ctype:
                results.append(CheckResult(rule="HTTP-CT", ok=True, message=f"Content-Type {ctype}", severity=Severity.INFO))
            else:
                results.append(CheckResult(rule="HTTP-CT", ok=False, message=f"Content-Type not JSON ({ctype or 'missing'})", severity=Severity.ERROR))

            try:
                raw = resp.json()
                results.append(CheckResult(rule="JSON-001", ok=True, message="JSON parsed", severity=Severity.INFO))
            except Exception as je:
                results.append(CheckResult(rule="JSON-001", ok=False, message=f"JSON parse error: {je}", severity=Severity.ERROR))

        except Exception as e:
            results.append(
                CheckResult(
                    rule="URL-001",
                    ok=False,
                    message=f"Card endpoint not reachable: {e}",
                    severity=Severity.ERROR,
                )
            )

        return card_url, raw, results

    def parse(self, raw: Dict[str, Any]) -> Tuple[AgentCard | None, List[CheckResult]]:
        results: List[CheckResult] = []
        try:
            card = AgentCard.model_validate(raw)
            results.append(
                CheckResult(
                    rule="CARD-STRUCT",
                    ok=True,
                    message="AgentCard parsed via schema",
                    severity=Severity.INFO,
                )
            )
            return card, results
        except ValidationError as ve:
            results.append(
                CheckResult(
                    rule="CARD-STRUCT",
                    ok=False,
                    message=f"Schema validation failed: {ve.errors()}",
                    severity=Severity.ERROR,
                )
            )
            return None, results
