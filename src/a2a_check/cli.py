from __future__ import annotations
import typer
from rich.console import Console

from .config import Settings
from .reporter import Reporter
from .http_client import HttpClient
from .card_service import CardService
from .checks.card_checks import CardChecks
from .checks.jsonrpc_checks import JsonRpcChecks
from .checks.rest_checks import RestChecks
from .jsonrpc_client import JsonRpcClient
from .suites.all import FullSuite
from .models import Section, Severity
from .helloworld.__main__ import main as hello_main

app = typer.Typer(add_completion=False, no_args_is_help=True)
card_app = typer.Typer(add_completion=False, no_args_is_help=True)
rpc_app = typer.Typer(add_completion=False, no_args_is_help=True)
rest_app = typer.Typer(add_completion=False, no_args_is_help=True)
net_app = typer.Typer(add_completion=False, no_args_is_help=True)
suite_app = typer.Typer(add_completion=False, no_args_is_help=True)


def _settings(
    timeout: float,
    insecure: bool,
    stream_timeout: float,
    well_known_path: str,
    auth_bearer: str | None,
):
    return Settings(
        timeout_s=timeout,
        verify_tls=not insecure,
        stream_timeout_s=stream_timeout,
        well_known_path=well_known_path,
        auth_bearer=auth_bearer,
    )


def _count_levels(sections: list[Section]) -> tuple[int, int, int]:
    ok = warn = err = 0
    for sec in sections:
        for r in sec.results:
            if r.ok:
                ok += 1
            elif getattr(r, "severity", None) == Severity.WARN:
                warn += 1
            else:
                err += 1
    return ok, warn, err


def _exit_code(reporter: Reporter, sections: list[Section], fail_on_warn: bool) -> int:
    code = reporter.summary_exit_code(sections)
    if fail_on_warn and code == 0:
        _, warn, _ = _count_levels(sections)
        if warn > 0:
            return 2
    return code


def _finalize(console: Console, reporter: Reporter, sections: list[Section], fail_on_warn: bool):
    code = _exit_code(reporter, sections, fail_on_warn)
    reporter.summary(sections)
    ok, warn, err = _count_levels(sections)
    suffix = " (fail-on-warn)" if fail_on_warn else ""
    console.print(f"[bold]Summary:[/bold] OK {ok} • WARN {warn} • ERROR {err} → exit {code}{suffix}")
    raise typer.Exit(code=code)


def _pick_url_for_transport(card: object, transport: str) -> str | None:
    pref = getattr(card, "preferred_transport", None) or getattr(card, "preferredTransport", None)
    url = getattr(card, "url", None)
    if pref == transport and url:
        return url
    add = getattr(card, "additional_interfaces", None) or getattr(card, "additionalInterfaces", None) or []
    for i in add:
        t = getattr(i, "transport", None) or (i.get("transport") if isinstance(i, dict) else None)
        u = getattr(i, "url", None) or (i.get("url") if isinstance(i, dict) else None)
        if t == transport and u:
            return u
    return None


@app.callback()
def main() -> None:
    pass


@net_app.command("probe")
def net_probe(
    target: str = typer.Argument(..., help="Base URL or AgentCard URL"),
    well_known_path: str = typer.Option("/.well-known/agent-card.json", "--well-known-path"),
    timeout: float = typer.Option(8.0, "--timeout"),
    insecure: bool = typer.Option(False, "--insecure"),
    auth_bearer: str | None = typer.Option(None, "--auth-bearer"),
    stream_timeout: float = typer.Option(12.0, "--stream-timeout"),
    fail_on_warn: bool = typer.Option(False, "--fail-on-warn", help="Return exit code 2 if warnings are present (and no errors)."),
):
    console = Console()
    reporter = Reporter(console)
    settings = _settings(timeout, insecure, stream_timeout, well_known_path, auth_bearer)
    http = HttpClient(settings)
    try:
        card_service = CardService(http, settings)
        _, _, net_results = card_service.fetch_raw(target, None)
        sections = [Section(title="Network", results=net_results)]
        for sec in sections:
            reporter.section(sec)
        _finalize(console, reporter, sections, fail_on_warn)
    finally:
        http.close()


@card_app.command("fetch")
def card_fetch(
    target: str = typer.Argument(..., help="Base URL or AgentCard URL"),
    card_url: str | None = typer.Option(None, "--card-url"),
    well_known_path: str = typer.Option("/.well-known/agent-card.json", "--well-known-path"),
    timeout: float = typer.Option(8.0, "--timeout"),
    insecure: bool = typer.Option(False, "--insecure"),
    auth_bearer: str | None = typer.Option(None, "--auth-bearer"),
    stream_timeout: float = typer.Option(12.0, "--stream-timeout"),
    fail_on_warn: bool = typer.Option(False, "--fail-on-warn", help="Return exit code 2 if warnings are present (and no errors)."),
):
    console = Console()
    reporter = Reporter(console)
    settings = _settings(timeout, insecure, stream_timeout, well_known_path, auth_bearer)
    http = HttpClient(settings)
    try:
        card_service = CardService(http, settings)
        resolved_url, raw, net_results = card_service.fetch_raw(target, card_url)
        sections = [Section(title="Network", results=net_results)]
        card, parse_results = card_service.parse(raw)
        sections.append(Section(title="Schema", results=parse_results))
        card_checks = CardChecks(resolved_url, raw, card)
        sections.append(card_checks.run_section())
        for sec in sections:
            reporter.section(sec)
        _finalize(console, reporter, sections, fail_on_warn)
    finally:
        http.close()


@card_app.command("validate")
def card_validate(
    target: str = typer.Argument(..., help="Base URL or AgentCard URL"),
    card_url: str | None = typer.Option(None, "--card-url"),
    well_known_path: str = typer.Option("/.well-known/agent-card.json", "--well-known-path"),
    timeout: float = typer.Option(8.0, "--timeout"),
    insecure: bool = typer.Option(False, "--insecure"),
    auth_bearer: str | None = typer.Option(None, "--auth-bearer"),
    stream_timeout: float = typer.Option(12.0, "--stream-timeout"),
    fail_on_warn: bool = typer.Option(False, "--fail-on-warn", help="Return exit code 2 if warnings are present (and no errors)."),
):
    return card_fetch(
        target=target,
        card_url=card_url,
        well_known_path=well_known_path,
        timeout=timeout,
        insecure=insecure,
        auth_bearer=auth_bearer,
        stream_timeout=stream_timeout,
        fail_on_warn=fail_on_warn,
    )


@rpc_app.command("ping")
def rpc_ping(
    jsonrpc_url: str = typer.Argument(..., help="JSON-RPC URL"),
    timeout: float = typer.Option(8.0, "--timeout"),
    insecure: bool = typer.Option(False, "--insecure"),
    auth_bearer: str | None = typer.Option(None, "--auth-bearer"),
    stream_timeout: float = typer.Option(12.0, "--stream-timeout"),
    fail_on_warn: bool = typer.Option(False, "--fail-on-warn", help="Return exit code 2 if warnings are present (and no errors)."),
):
    console = Console()
    reporter = Reporter(console)
    settings = _settings(timeout, insecure, stream_timeout, "/.well-known/agent-card.json", auth_bearer)
    http = HttpClient(settings)
    try:
        client = JsonRpcClient(http, settings, jsonrpc_url)
        from a2a.types import AgentCard
        fake = AgentCard.model_validate(
            {
                "protocolVersion": "0.3.0",
                "name": "temp",
                "description": "temp",
                "url": jsonrpc_url,
                "preferredTransport": "JSONRPC",
                "version": "1.0.0",
                "capabilities": {"streaming": False},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["application/json"],
                "skills": [],
            }
        )
        checks = JsonRpcChecks(fake, jsonrpc_url, client)
        section = checks.run_section()
        reporter.section(section)
        _finalize(console, reporter, [section], fail_on_warn)
    finally:
        http.close()


@rpc_app.command("ping-from-card")
def rpc_ping_from_card(
    target: str = typer.Argument(..., help="Base URL or AgentCard URL"),
    card_url: str | None = typer.Option(None, "--card-url"),
    well_known_path: str = typer.Option("/.well-known/agent-card.json", "--well-known-path"),
    timeout: float = typer.Option(8.0, "--timeout"),
    insecure: bool = typer.Option(False, "--insecure"),
    auth_bearer: str | None = typer.Option(None, "--auth-bearer"),
    stream_timeout: float = typer.Option(12.0, "--stream-timeout"),
    fail_on_warn: bool = typer.Option(False, "--fail-on-warn", help="Return exit code 2 if warnings are present (and no errors)."),
):
    console = Console()
    reporter = Reporter(console)
    settings = _settings(timeout, insecure, stream_timeout, well_known_path, auth_bearer)
    http = HttpClient(settings)
    try:
        card_service = CardService(http, settings)
        resolved_url, raw, net_results = card_service.fetch_raw(target, card_url)
        sections: list[Section] = [Section(title="Network", results=net_results)]
        card, parse_results = card_service.parse(raw)
        sections.append(Section(title="Schema", results=parse_results))
        if not card:
            for sec in sections:
                reporter.section(sec)
            _finalize(console, reporter, sections, fail_on_warn)
        jsonrpc_url: str | None = None
        pref = card.preferred_transport or "JSONRPC"
        if pref == "JSONRPC":
            jsonrpc_url = card.url
        elif card.additional_interfaces:
            for i in card.additional_interfaces:
                if i.transport == "JSONRPC":
                    jsonrpc_url = i.url
                    break
        if not jsonrpc_url:
            from .models import CheckResult
            sections.append(Section(
                title="JSON-RPC",
                results=[CheckResult(rule="RPC-URL", ok=False, message="Agent does not declare a JSON-RPC interface", severity=Severity.ERROR)]
            ))
            for sec in sections:
                reporter.section(sec)
            raise typer.Exit(code=1)
        client = JsonRpcClient(http, settings, jsonrpc_url)
        from a2a.types import AgentCard as _AC
        fake = _AC.model_validate(
            {
                "protocolVersion": "0.3.0",
                "name": "temp",
                "description": "temp",
                "url": jsonrpc_url,
                "preferredTransport": "JSONRPC",
                "version": "1.0.0",
                "capabilities": {"streaming": False},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["application/json"],
                "skills": [],
            }
        )
        checks = JsonRpcChecks(fake, jsonrpc_url, client)
        sections.append(checks.run_section())
        for sec in sections:
            reporter.section(sec)
        _finalize(console, reporter, sections, fail_on_warn)
    finally:
        http.close()


@rpc_app.command("stream")
def rpc_stream(
    jsonrpc_url: str = typer.Argument(..., help="JSON-RPC URL"),
    text: str = typer.Option("stream test", "--text"),
    timeout: float = typer.Option(8.0, "--timeout"),
    insecure: bool = typer.Option(False, "--insecure"),
    auth_bearer: str | None = typer.Option(None, "--auth-bearer"),
    stream_timeout: float = typer.Option(12.0, "--stream-timeout"),
    fail_on_warn: bool = typer.Option(False, "--fail-on-warn", help="Return exit code 2 if warnings are present (and no errors)."),
):
    console = Console()
    reporter = Reporter(console)
    settings = _settings(timeout, insecure, stream_timeout, "/.well-known/agent-card.json", auth_bearer)
    http = HttpClient(settings)
    try:
        client = JsonRpcClient(http, settings, jsonrpc_url)
        try:
            _, events = client.stream_text(text)
            ok = len(events) > 0
            from .models import CheckResult, Section as _Section
            section = _Section(
                title="JSON-RPC Stream",
                results=[
                    CheckResult(
                        rule="RPC-STREAM",
                        ok=ok,
                        message="received SSE events" if ok else "no SSE events",
                        severity=Severity.ERROR if not ok else Severity.INFO,
                    )
                ],
            )
        except Exception as e:
            from .models import CheckResult, Section as _Section
            section = _Section(
                title="JSON-RPC Stream",
                results=[
                    CheckResult(
                        rule="RPC-STREAM",
                        ok=False,
                        message=f"stream error {e}",
                        severity=Severity.ERROR,
                    )
                ],
            )
        reporter.section(section)
        _finalize(console, reporter, [section], fail_on_warn)
    finally:
        http.close()


# ----------------------- REST commands -----------------------

@rest_app.command("check")
def rest_check(
    rest_base: str = typer.Argument(..., help="REST base URL (e.g. http://host or http://host/v1)"),
    timeout: float = typer.Option(8.0, "--timeout"),
    insecure: bool = typer.Option(False, "--insecure"),
    auth_bearer: str | None = typer.Option(None, "--auth-bearer"),
    stream_timeout: float = typer.Option(12.0, "--stream-timeout"),
    fail_on_warn: bool = typer.Option(False, "--fail-on-warn", help="Return exit code 2 if warnings are present (and no errors)."),
):
    """Run HTTP+JSON checks directly against a REST base (no proxy)."""
    console = Console()
    reporter = Reporter(console)
    settings = _settings(timeout, insecure, stream_timeout, "/.well-known/agent-card.json", auth_bearer)
    http = HttpClient(settings)
    try:
        checks = RestChecks(http, settings, rest_base_url=rest_base)
        section = checks.run_section()
        reporter.section(section)
        _finalize(console, reporter, [section], fail_on_warn)
    finally:
        http.close()


@rest_app.command("check-from-card")
def rest_check_from_card(
    target: str = typer.Argument(..., help="Base URL or AgentCard URL"),
    card_url: str | None = typer.Option(None, "--card-url"),
    well_known_path: str = typer.Option("/.well-known/agent-card.json", "--well-known-path"),
    timeout: float = typer.Option(8.0, "--timeout"),
    insecure: bool = typer.Option(False, "--insecure"),
    auth_bearer: str | None = typer.Option(None, "--auth-bearer"),
    stream_timeout: float = typer.Option(12.0, "--stream-timeout"),
    fail_on_warn: bool = typer.Option(False, "--fail-on-warn", help="Return exit code 2 if warnings are present (and no errors)."),
):
    """Resolve HTTP+JSON base from the AgentCard and run REST checks."""
    console = Console()
    reporter = Reporter(console)
    settings = _settings(timeout, insecure, stream_timeout, well_known_path, auth_bearer)
    http = HttpClient(settings)
    try:
        card_service = CardService(http, settings)
        resolved_url, raw, net_results = card_service.fetch_raw(target, card_url)
        sections: list[Section] = [Section(title="Network", results=net_results)]
        card, parse_results = card_service.parse(raw)
        sections.append(Section(title="Schema", results=parse_results))
        if not card:
            for sec in sections:
                reporter.section(sec)
            _finalize(console, reporter, sections, fail_on_warn)

        rest_base = _pick_url_for_transport(card, "HTTP+JSON")
        if not rest_base:
            from .models import CheckResult
            sections.append(Section(
                title="HTTP+JSON",
                results=[CheckResult(rule="REST-URL", ok=False, message="Agent does not declare an HTTP+JSON interface", severity=Severity.ERROR)]
            ))
            for sec in sections:
                reporter.section(sec)
            raise typer.Exit(code=1)

        checks = RestChecks(http, settings, rest_base_url=rest_base)
        sections.append(checks.run_section())
        for sec in sections:
            reporter.section(sec)
        _finalize(console, reporter, sections, fail_on_warn)
    finally:
        http.close()


@suite_app.command("all")
def suite_all(
    target: str = typer.Argument(..., help="Base URL or AgentCard URL"),
    card_url: str | None = typer.Option(None, "--card-url"),
    well_known_path: str = typer.Option("/.well-known/agent-card.json", "--well-known-path"),
    timeout: float = typer.Option(8.0, "--timeout"),
    insecure: bool = typer.Option(False, "--insecure"),
    auth_bearer: str | None = typer.Option(None, "--auth-bearer"),
    stream_timeout: float = typer.Option(12.0, "--stream-timeout"),
    fail_on_warn: bool = typer.Option(False, "--fail-on-warn", help="Return exit code 2 if warnings are present (and no errors)."),
):
    console = Console()
    reporter = Reporter(console)
    settings = _settings(timeout, insecure, stream_timeout, well_known_path, auth_bearer)
    suite = FullSuite(settings)
    sections = suite.run(target, card_url)
    for sec in sections:
        reporter.section(sec)
    _finalize(console, reporter, sections, fail_on_warn)


@app.command("start_dummy")
def start_dummy(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address for the dummy A2A server"),
    port: int = typer.Option(9999, "--port", help="Port for the dummy A2A server"),
    mode: str = typer.Option(
        None,
        "--mode",
        help='Dummy mode: "ok", "errors", or "mixed". If omitted, falls back to --wrong behavior.',
    ),
    wrong: bool = typer.Option(False, "--wrong", help="Deprecated. Maps to --mode errors when set."),
):
    if mode is not None:
        m = mode.strip().lower()
        if m not in ("ok", "errors", "mixed"):
            raise typer.BadParameter('Invalid --mode. Use "ok", "errors", or "mixed".')
    else:
        m = "errors" if wrong else "ok"
    hello_main(host=host, port=port, mode=m)


@app.command("ui")
def ui(
    target: str | None = typer.Option(None, "--target", help="Optional: Base URL oder AgentCard URL für Vorkonfiguration."),
    rest_base: str | None = typer.Option(None, "--rest-base", help="Optional: direkte REST-Base."),
    card_url: str | None = typer.Option(None, "--card-url"),
    well_known_path: str = typer.Option("/.well-known/agent-card.json", "--well-known-path"),
    timeout: float = typer.Option(8.0, "--timeout"),
    insecure: bool = typer.Option(False, "--insecure"),
    auth_bearer: str | None = typer.Option(None, "--auth-bearer"),
    stream_timeout: float = typer.Option(12.0, "--stream-timeout"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(5173, "--port"),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
):
    try:
        from .ui_server import run_ui
    except Exception:
        typer.secho("UI-Komponenten fehlen. Installiere: pip install 'a2a-check[ui]'", fg=typer.colors.YELLOW)
        raise typer.Exit(code=2)

    # Optional: Vorkonfiguration aus --target/--card-url
    resolved_rest = rest_base
    if not resolved_rest and target:
        settings = _settings(timeout, insecure, stream_timeout, well_known_path, auth_bearer)
        http = HttpClient(settings)
        try:
            cs = CardService(http, settings)
            _, raw, _ = cs.fetch_raw(target, card_url)
            card, _ = cs.parse(raw)
            if card:
                resolved_rest = _pick_url_for_transport(card, "HTTP+JSON")
        finally:
            http.close()

    run_ui(
        rest_base=resolved_rest,
        auth_bearer=auth_bearer,
        host=host,
        port=port,
        verify_tls=not insecure,
        timeout_s=timeout,
        stream_timeout_s=stream_timeout,
        well_known_path=well_known_path,
        open_browser=open_browser,
    )


app.add_typer(net_app, name="net")
app.add_typer(card_app, name="card")
app.add_typer(rpc_app, name="rpc")
app.add_typer(rest_app, name="rest")
app.add_typer(suite_app, name="suite")

if __name__ == "__main__":
    app()
