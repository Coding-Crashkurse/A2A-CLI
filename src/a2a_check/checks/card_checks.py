from __future__ import annotations
from typing import List, Dict, Any, Set, Optional
from urllib.parse import urlparse
import re

from a2a.types import AgentCard
from ..models import CheckResult, Section, Severity


SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")


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
        s.extend(self._provider_and_meta())
        return s

    # ---------------------------
    # Core fields
    # ---------------------------
    def _core_presence(self) -> List[CheckResult]:
        out: List[CheckResult] = []

        pv = self.raw.get("protocolVersion") or self.raw.get("protocol_version")
        out.append(CheckResult(
            rule="CARD-001",
            ok=pv is not None,
            message="protocolVersion present" if pv else "protocolVersion missing",
            severity=Severity.ERROR
        ))
        if pv is not None:
            ok_proto = bool(pv == "dev" or (isinstance(pv, str) and pv.startswith("0.3.")))
            out.append(CheckResult(
                rule="CARD-001a",
                ok=ok_proto,
                message=f"protocolVersion acceptable ({pv})" if ok_proto else f"protocolVersion unexpected ({pv}); expected 0.3.x or dev",
                severity=Severity.WARN if not ok_proto else Severity.INFO
            ))

        name_ok = bool(self.raw.get("name"))
        out.append(CheckResult(rule="CARD-002", ok=name_ok, message="name present" if name_ok else "name missing", severity=Severity.ERROR))

        desc_ok = bool(self.raw.get("description"))
        out.append(CheckResult(rule="CARD-004", ok=desc_ok, message="description present" if desc_ok else "description missing", severity=Severity.ERROR))

        url_ok = bool(self.raw.get("url"))
        out.append(CheckResult(rule="CARD-003", ok=url_ok, message="url present" if url_ok else "url missing", severity=Severity.ERROR))

        version = self.raw.get("version")
        v_ok = isinstance(version, str) and len(version) > 0
        out.append(CheckResult(rule="CARD-005", ok=v_ok, message="version present" if v_ok else "version missing", severity=Severity.ERROR))
        if v_ok and not SEMVER_RE.match(version):
            out.append(CheckResult(rule="CARD-005a", ok=False, message=f"version not semver-like: {version}", severity=Severity.WARN))

        dim = self.raw.get("defaultInputModes")
        dom = self.raw.get("defaultOutputModes")
        dim_ok = isinstance(dim, list) and all(isinstance(x, str) for x in dim) and len(dim) > 0
        dom_ok = isinstance(dom, list) and all(isinstance(x, str) for x in dom) and len(dom) > 0
        out.append(CheckResult(rule="CARD-006", ok=dim_ok, message="defaultInputModes present" if dim_ok else "defaultInputModes missing/invalid", severity=Severity.ERROR))
        out.append(CheckResult(rule="CARD-007", ok=dom_ok, message="defaultOutputModes present" if dom_ok else "defaultOutputModes missing/invalid", severity=Severity.ERROR))

        return out

    # ---------------------------
    # Transports
    # ---------------------------
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

        # Must declare at least one transport
        atleast_one = (len(ai) > 0) or bool(pref and url)
        out.append(CheckResult(rule="CARD-013", ok=atleast_one, message="at least one transport declared" if atleast_one else "no transports declared", severity=Severity.ERROR))

        # Preferred transport must be available at main URL and appear in additionalInterfaces for completeness
        matches = any((i.get("url") == url and i.get("transport") == pref) for i in ai)
        out.append(CheckResult(rule="CARD-011", ok=bool(matches), message="preferredTransport matches additionalInterfaces" if matches else "preferredTransport/url mismatch or missing additionalInterfaces entry", severity=Severity.ERROR))

        # No conflicting transport declarations per URL
        transports: Dict[str, Set[str]] = {}
        for i in ai:
            transports.setdefault(i.get("url", ""), set()).add(i.get("transport", ""))
        conflicts = any(len(v) > 1 for v in transports.values())
        out.append(CheckResult(rule="CARD-012", ok=not conflicts, message="no transport conflicts" if not conflicts else "conflicting transport declarations", severity=Severity.ERROR))

        # Validate transport values where present
        valid_vals = {"JSONRPC", "GRPC", "HTTP+JSON"}
        bad_vals = [i.get("transport") for i in ai if i.get("transport") not in valid_vals]
        if pref and pref not in valid_vals:
            bad_vals.append(pref)
        out.append(CheckResult(
            rule="CARD-016",
            ok=len(bad_vals) == 0,
            message="transports use standard values" if len(bad_vals) == 0 else f"non-standard transport(s): {sorted(set(bad_vals))}",
            severity=Severity.WARN if len(bad_vals) else Severity.INFO
        ))

        return out

    # ---------------------------
    # Capabilities
    # ---------------------------
    def _capabilities(self) -> List[CheckResult]:
        out: List[CheckResult] = []
        caps = self.raw.get("capabilities") or {}
        st = caps.get("streaming")
        pn = caps.get("pushNotifications") if "pushNotifications" in caps else caps.get("push_notifications")
        sth = caps.get("stateTransitionHistory") if "stateTransitionHistory" in caps else caps.get("state_transition_history")
        out.append(CheckResult(rule="CARD-020", ok=isinstance(st, bool) or st is None, message="capabilities.streaming boolean or absent", severity=Severity.ERROR))
        out.append(CheckResult(rule="CARD-021", ok=isinstance(pn, bool) or pn is None, message="capabilities.pushNotifications boolean or absent", severity=Severity.ERROR))
        out.append(CheckResult(rule="CARD-022", ok=isinstance(sth, bool) or sth is None, message="capabilities.stateTransitionHistory boolean or absent", severity=Severity.ERROR))

        exts = caps.get("extensions")
        if exts is not None:
            ok_exts = isinstance(exts, list) and all(isinstance(x, dict) and "uri" in x for x in exts)
            out.append(CheckResult(rule="CARD-023", ok=ok_exts, message="capabilities.extensions valid" if ok_exts else "capabilities.extensions invalid", severity=Severity.WARN if not ok_exts else Severity.INFO))
        return out

    # ---------------------------
    # Skills
    # ---------------------------
    def _skills(self) -> List[CheckResult]:
        out: List[CheckResult] = []
        skills = self.raw.get("skills") or []
        nonempty = len(skills) > 0
        out.append(CheckResult(rule="CARD-030", ok=nonempty, message="skills present" if nonempty else "skills missing", severity=Severity.ERROR))

        ids = [s.get("id") for s in skills if isinstance(s, dict)]
        unique = len(ids) == len(set(ids))
        out.append(CheckResult(rule="CARD-031", ok=unique, message="skill ids unique" if unique else "duplicate skill ids", severity=Severity.ERROR))

        # Each skill must have a description
        missing_desc = [s.get("id") for s in skills if isinstance(s, dict) and not s.get("description")]
        out.append(CheckResult(
            rule="CARD-032",
            ok=len(missing_desc) == 0,
            message="all skills have description" if len(missing_desc) == 0 else f"skills missing description: {missing_desc}",
            severity=Severity.ERROR if missing_desc else Severity.INFO
        ))

        # Tags are recommended to be non-empty arrays
        bad_tags = [s.get("id") for s in skills if isinstance(s, dict) and (("tags" not in s) or not isinstance(s.get("tags"), list) or len(s["tags"]) == 0)]
        out.append(CheckResult(
            rule="CARD-033",
            ok=len(bad_tags) == 0,
            message="skills have non-empty tags" if len(bad_tags) == 0 else f"skills with empty/missing tags: {bad_tags}",
            severity=Severity.WARN if bad_tags else Severity.INFO
        ))
        return out

    # ---------------------------
    # Security
    # ---------------------------
    def _security(self) -> List[CheckResult]:
        out: List[CheckResult] = []
        sec = self.raw.get("security") or []
        schemes = self.raw.get("securitySchemes") or self.raw.get("security_schemes") or {}
        if not sec:
            out.append(CheckResult(rule="CARD-040", ok=True, message="security optional and absent", severity=Severity.INFO))
        else:
            keys: Set[str] = set()
            for req in sec:
                if isinstance(req, dict):
                    for k in req.keys():
                        keys.add(k)
            covers = keys.issubset(set(schemes.keys()))
            out.append(CheckResult(rule="CARD-041", ok=covers, message="security schemes declared" if covers else "security references missing in securitySchemes", severity=Severity.ERROR))

        # If supportsAuthenticatedExtendedCard=True, it's recommended to declare schemes
        saec = self.raw.get("supportsAuthenticatedExtendedCard")
        if saec is True and not schemes:
            out.append(CheckResult(rule="CARD-043", ok=False, message="supportsAuthenticatedExtendedCard=true but no securitySchemes declared", severity=Severity.WARN))
        return out

    # ---------------------------
    # Provider / iconUrl / misc
    # ---------------------------
    def _provider_and_meta(self) -> List[CheckResult]:
        out: List[CheckResult] = []
        prov = self.raw.get("provider")
        if prov is not None:
            ok = isinstance(prov, dict) and isinstance(prov.get("organization"), str) and isinstance(prov.get("url"), str)
            out.append(CheckResult(rule="CARD-050", ok=ok, message="provider valid" if ok else "provider invalid (expect organization/url)", severity=Severity.WARN if not ok else Severity.INFO))

        icon = self.raw.get("iconUrl")
        if icon:
            parsed = urlparse(icon)
            ok_icon = parsed.scheme in ("http", "https") and bool(parsed.netloc)
            out.append(CheckResult(rule="CARD-051", ok=ok_icon, message="iconUrl looks valid" if ok_icon else f"iconUrl invalid: {icon}", severity=Severity.WARN if not ok_icon else Severity.INFO))
        return out
