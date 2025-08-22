from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass
class CheckResult:
    """Atomic result of a single validation rule."""
    rule: str
    ok: bool
    message: str
    severity: Severity = Severity.ERROR
    data: Optional[Dict[str, Any]] = None


@dataclass
class Section:
    """Collection of related check results."""
    title: str
    results: List[CheckResult] = field(default_factory=list)

    def extend(self, items: List[CheckResult]) -> None:
        self.results.extend(items)

    def has_failures(self) -> bool:
        return any((not r.ok) and r.severity == Severity.ERROR for r in self.results)

    def has_warnings(self) -> bool:
        return any((not r.ok) and r.severity == Severity.WARN for r in self.results)
