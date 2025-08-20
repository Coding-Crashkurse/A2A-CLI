from __future__ import annotations
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from .models import CheckResult, Section, Severity


class Reporter:
    """Renders sections and check results using rich."""

    def __init__(self, console: Console) -> None:
        self.console = console

    def section(self, section: Section) -> None:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Status", width=8)
        table.add_column("Rule", style="bold")
        table.add_column("Message")
        for r in section.results:
            status = "✅" if r.ok else ("⚠️" if r.severity == Severity.WARN else "❌")
            table.add_row(status, r.rule, r.message)
        panel_title = Text(section.title, style="bold blue")
        self.console.print(Panel.fit(table, title=panel_title))

    def summary_exit_code(self, sections: list[Section]) -> int:
        has_error = any(s.has_failures() for s in sections)
        return 1 if has_error else 0
