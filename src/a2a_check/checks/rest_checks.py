from __future__ import annotations
from typing import List
from ..models import CheckResult, Section, Severity


class RestChecks:
    """Placeholder for HTTP+JSON mapping checks."""

    def run_section(self) -> Section:
        s = Section(title="HTTP+JSON")
        s.extend([CheckResult(rule="REST-000", ok=True, message="no REST checks implemented", severity=Severity.INFO)])
        return s
