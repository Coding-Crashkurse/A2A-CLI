from __future__ import annotations
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from .models import Section, Severity


class Reporter:
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

    def summary(self, sections: list[Section]) -> None:
        ok = 0
        warn = 0
        err = 0
        for s in sections:
            for r in s.results:
                if r.ok:
                    ok += 1
                elif r.severity == Severity.WARN:
                    warn += 1
                else:
                    err += 1
        table = Table(show_header=True, header_style="bold")
        table.add_column("OK")
        table.add_column("WARN")
        table.add_column("ERROR")
        table.add_row(str(ok), str(warn), str(err))
        if err > 0:
            style = "bold red"
        elif warn > 0:
            style = "bold yellow"
        else:
            style = "bold green"
        self.console.print(Panel.fit(table, title=Text("Summary", style=style)))
