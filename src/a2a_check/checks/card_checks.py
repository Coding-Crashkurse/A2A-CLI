from __future__ import annotations
from typing import List, Dict, Any, Tuple, Set
from a2a.types import AgentCard
from ..models import CheckResult, Section, Severity


class CardChecks:
    """Runs structural and semantic checks for AgentCard."""

    def __init__(self, card_url: str, raw: Dict[str, Any], card: AgentCard | None) -> None:
        self.card_url = card_url
        self.raw = raw
        self.card = card

    def run_section(self) -> Section:
        s = Section(title="AgentCard")
        s.extend(self._core_presence())
        s.extend(self._transports())
        s.extend(self._capabilities())
        s.extend(self._skills())
        s.extend(self._security())
        s.extend(self._metadata())
        return s

    def _core_presence(self) -> List[CheckResult]:
        out: List[CheckResult] = []
        pv = self.raw.get("protocolVersion") or self.raw.get("protocol_version")
        ok_pv = pv is not None
        out.append(CheckResult(rule="CARD-001", ok=bool(ok_pv), message="protocolVersion present" if ok_pv else "protocolVersion missing", severity=Severity.ERROR))
        name_ok = bool(self.raw.get("name"))
        out.append(CheckResult(rule="CARD-002", ok=name_ok, message="name present" if name_ok else "name missing", severity=Severity.ERROR))
        url_ok = bool(self.raw.get("url"))
        out.append(CheckResult(rule="CARD-003", ok=url_ok, message="url present" if url_ok else "url missing", severity=Severity.ERROR))
        return out

    def _transports(self) -> List[CheckResult]:
        out: List[CheckResult] = []
        if not self.card:
            out.append(CheckResult(rule="CARD-TR-STRUCT", ok=False, message="Card not parsed", severity=Severity.ERROR))
            return out
        pref = self.raw.get("preferredTransport") or self.raw.get("preferred_transport")
        url = self.raw.get("url")
        ai = self.raw.get("additionalInterfaces") or self.raw.get("additional_interfaces") or []
        has_pref = bool(pref)
        out.append(CheckResult(rule="CARD-010", ok=has_pref, message="preferredTransport present" if has_pref else "preferredTransport missing", severity=Severity.ERROR))
        matches = any((i.get("url") == url and i.get("transport") == pref) for i in ai)
        out.append(CheckResult(rule="CARD-011", ok=bool(matches), message="preferredTransport matches additionalInterfaces" if matches else "preferredTransport/url mismatch", severity=Severity.ERROR))
        transports: Dict[str, Set[str]] = {}
        for i in ai:
            transports.setdefault(i.get("url", ""), set()).add(i.get("transport", ""))
        conflicts = any(len(v) > 1 for v in transports.values())
        out.append(CheckResult(rule="CARD-012", ok=not conflicts, message="no transport conflicts" if not conflicts else "conflicting transport declarations", severity=Severity.ERROR))
        atleast_one = len(ai) > 0 or bool(pref and url)
        out.append(CheckResult(rule="CARD-013", ok=atleast_one, message="at least one transport declared" if atleast_one else "no transports declared", severity=Severity.ERROR))
        return out

    def _capabilities(self) -> List[CheckResult]:
        out: List[CheckResult] = []
        caps = self.raw.get("capabilities") or {}
        st = caps.get("streaming")
        pn = caps.get("pushNotifications") if "pushNotifications" in caps else caps.get("push_notifications")
        sth = caps.get("stateTransitionHistory") if "stateTransitionHistory" in caps else caps.get("state_transition_history")
        out.append(CheckResult(rule="CARD-020", ok=isinstance(st, bool) or st is None, message="capabilities.streaming boolean or absent", severity=Severity.ERROR))
        out.append(CheckResult(rule="CARD-021", ok=isinstance(pn, bool) or pn is None, message="capabilities.pushNotifications boolean or absent", severity=Severity.ERROR))
        out.append(CheckResult(rule="CARD-022", ok=isinstance(sth, bool) or sth is None, message="capabilities.stateTransitionHistory boolean or absent", severity=Severity.ERROR))
        return out

    def _skills(self) -> List[CheckResult]:
        out: List[CheckResult] = []
        skills = self.raw.get("skills") or []
        nonempty = len(skills) > 0
        out.append(CheckResult(rule="CARD-030", ok=nonempty, message="skills present" if nonempty else "skills missing", severity=Severity.ERROR))
        ids = [s.get("id") for s in skills if isinstance(s, dict)]
        unique = len(ids) == len(set(ids))
        out.append(CheckResult(rule="CARD-031", ok=unique, message="skill ids unique" if unique else "duplicate skill ids", severity=Severity.ERROR))
        return out

    def _security(self) -> List[CheckResult]:
        out: List[CheckResult] = []
        sec = self.raw.get("security") or []
        schemes = self.raw.get("securitySchemes") or self.raw.get("security_schemes") or {}
        if not sec:
            out.append(CheckResult(rule="CARD-040", ok=True, message="security optional and absent", severity=Severity.INFO))
            return out
        keys: Set[str] = set()
        for req in sec:
            for k in req.keys():
                keys.add(k)
        covers = keys.issubset(set(schemes.keys()))
        out.append(CheckResult(rule="CARD-041", ok=covers, message="security schemes declared" if covers else "security references missing in securitySchemes", severity=Severity.ERROR))
        return out

    def _metadata(self) -> List[CheckResult]:
        out: List[CheckResult] = []
        return out
