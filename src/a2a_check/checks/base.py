from __future__ import annotations
from typing import Protocol, List
from ..models import CheckResult, Section


class Check(Protocol):
    """Protocol for a single check."""

    def run(self) -> List[CheckResult]:
        ...


class SectionedCheck(Protocol):
    """Protocol for a grouped checker that returns a complete section."""

    def run_section(self) -> Section:
        ...
