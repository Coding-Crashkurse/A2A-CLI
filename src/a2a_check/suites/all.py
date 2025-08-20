from __future__ import annotations
from typing import List, Optional
from a2a.types import AgentCard
from ..config import Settings
from ..http_client import HttpClient
from ..card_service import CardService
from ..jsonrpc_client import JsonRpcClient
from ..models import Section
from ..checks.card_checks import CardChecks
from ..checks.jsonrpc_checks import JsonRpcChecks
from ..checks.rest_checks import RestChecks
from ..util import resolve_card_url


class FullSuite:
    """Runs the end-to-end compliance suite for a givsen target."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, target: str, card_url_override: str | None = None) -> list[Section]:
        http = HttpClient(self.settings)
        try:
            card_service = CardService(http, self.settings)
            resolved_url, raw, net_results = card_service.fetch_raw(target, card_url_override)
            sections: list[Section] = [Section(title="Network", results=net_results)]
            card, parse_results = card_service.parse(raw)
            sections.append(Section(title="Schema", results=parse_results))
            card_checks = CardChecks(resolved_url, raw, card)
            sections.append(card_checks.run_section())
            if card:
                jsonrpc_url = self._pick_jsonrpc_url(card)
                if jsonrpc_url:
                    client = JsonRpcClient(http, self.settings, jsonrpc_url)
                    jr = JsonRpcChecks(card, jsonrpc_url, client)
                    sections.append(jr.run_section())
                else:
                    sections.append(Section(title="JSON-RPC", results=[]))
            return sections
        finally:
            http.close()

    def _pick_jsonrpc_url(self, card: AgentCard) -> Optional[str]:
        pref = card.preferred_transport or "JSONRPC"
        if pref == "JSONRPC":
            return card.url
        if card.additional_interfaces:
            for i in card.additional_interfaces:
                if i.transport == "JSONRPC":
                    return i.url
        return None
