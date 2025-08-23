from __future__ import annotations
from typing import List, Optional, Any

from ..config import Settings
from ..http_client import HttpClient
from ..card_service import CardService
from ..checks.card_checks import CardChecks
from ..checks.jsonrpc_checks import JsonRpcChecks
from ..checks.rest_checks import RestChecks
from ..jsonrpc_client import JsonRpcClient
from ..models import Section, CheckResult, Severity


class FullSuite:
    """
    End-to-end suite:
      - Network + fetch card
      - Schema parse
      - CardChecks
      - JSON-RPC checks (if available)
      - REST checks (if available)
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _pick_url_for_transport(self, card: Any, transport: str) -> Optional[str]:
        # preferred?
        pref = getattr(card, "preferred_transport", None) or getattr(
            card, "preferredTransport", None
        )
        url = getattr(card, "url", None)
        if pref == transport and url:
            return url
        # additional interfaces
        add = (
            getattr(card, "additional_interfaces", None)
            or getattr(card, "additionalInterfaces", None)
            or []
        )
        for i in add:
            if (getattr(i, "transport", None) or i.get("transport")) == transport:
                return getattr(i, "url", None) or i.get("url")
        return None

    def run(self, target: str, override_card_url: Optional[str]) -> List[Section]:
        http = HttpClient(self.settings)
        try:
            sections: List[Section] = []
            card_service = CardService(http, self.settings)

            # Network + fetch raw
            resolved_url, raw, net_results = card_service.fetch_raw(
                target, override_card_url
            )
            sections.append(Section(title="Network", results=net_results))

            # Schema
            card, parse_results = card_service.parse(raw)
            sections.append(Section(title="Schema", results=parse_results))

            # Card checks
            card_checks = CardChecks(resolved_url, raw, card)
            sections.append(card_checks.run_section())

            if not card:
                return sections

            # JSON-RPC
            jsonrpc_url = self._pick_url_for_transport(card, "JSONRPC")
            if jsonrpc_url:
                client = JsonRpcClient(http, self.settings, jsonrpc_url)
                sections.append(JsonRpcChecks(card, jsonrpc_url, client).run_section())
            else:
                sections.append(
                    Section(
                        title="JSON-RPC",
                        results=[
                            CheckResult(
                                rule="RPC-URL",
                                ok=False,
                                message="Agent declares no JSON-RPC interface",
                                severity=Severity.WARN,
                            )
                        ],
                    )
                )

            # REST
            rest_url = self._pick_url_for_transport(card, "HTTP+JSON")
            if rest_url:
                sections.append(RestChecks(http, self.settings, rest_url).run_section())
            else:
                sections.append(
                    Section(
                        title="HTTP+JSON",
                        results=[
                            CheckResult(
                                rule="REST-URL",
                                ok=True,
                                message="No HTTP+JSON interface declared (skip)",
                                severity=Severity.INFO,
                            )
                        ],
                    )
                )

            return sections
        finally:
            http.close()
