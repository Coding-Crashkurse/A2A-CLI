# src/a2a_check/cli.py
from __future__ import annotations
import typer
from rich.console import Console

from .config import Settings
from .reporter import Reporter
from .http_client import HttpClient
from .card_service import CardService
from .checks.card_checks import CardChecks
from .checks.jsonrpc_checks import JsonRpcChecks
from .jsonrpc_client import JsonRpcClient
from .suites.all import FullSuite
from .models import Section

# Static import of the dummy server (no try/except fallback)
from .helloworld.__main__ import main as hello_main  # type: ignore

app = typer.Typer(add_completion=False, no_args_is_help=True)
card_app = typer.Typer(add_completion=False, no_args_is_help=True)
rpc_app = typer.Typer(add_completion=False, no_args_is_help=True)
net_app = typer.Typer(add_completion=False, no_args_is_help=True)
suite_app = typer.Typer(add_completion=False, no_args_is_help=True)


def _settings(timeout: float, insecure: bool, stream_timeout: float, well_known_path: str, auth_bearer: str | None):
    return Settings(
        timeout_s=timeout,
        verify_tls=not insecure,
        stream_timeout_s=stream_timeout,
        well_known_path=well_known_path,
        auth_bearer=auth_bearer,
    )


@app.callback()
def main() -> None:
    """A2A compliance checker CLI."""


@net_app.command("probe")
def net_probe(
    target: str = typer.Argument(..., help="Base URL or AgentCard URL"),
    well_known_path: str = typer.Option("/.well-known/agent-card.json", "--well-known-path"),
    timeout: float = typer.Option(8.0, "--timeout"),
    insecure: bool = typer.Option(False, "--insecure"),
    auth_bearer: str | None = typer.Option(None, "--auth-bearer"),
    stream_timeout: float = typer.Option(12.0, "--stream-timeout"),
):
    console = Console()
    reporter = Reporter(console)
    settings = _settings(timeout, insecure, stream_timeout, well_known_path, auth_bearer)
    http = HttpClient(settings)
    try:
        card_service = CardService(http, settings)
        resolved_url, raw, net_results = card_service.fetch_raw(target, None)
        sections = [Section(title="Network", results=net_results)]
        for sec in sections:
            reporter.section(sec)
        raise typer.Exit(code=reporter.summary_exit_code(sections))
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
        raise typer.Exit(code=reporter.summary_exit_code(sections))
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
):
    # Direct call (no .callback indirection)
    return card_fetch(
        target=target,
        card_url=card_url,
        well_known_path=well_known_path,
        timeout=timeout,
        insecure=insecure,
        auth_bearer=auth_bearer,
        stream_timeout=stream_timeout,
    )


@rpc_app.command("ping")
def rpc_ping(
    jsonrpc_url: str = typer.Argument(..., help="JSON-RPC URL"),
    timeout: float = typer.Option(8.0, "--timeout"),
    insecure: bool = typer.Option(False, "--insecure"),
    auth_bearer: str | None = typer.Option(None, "--auth-bearer"),
    stream_timeout: float = typer.Option(12.0, "--stream-timeout"),
):
    """
    Ping a JSON-RPC URL (streaming intentionally disabled in this command).
    """
    console = Console()
    reporter = Reporter(console)
    settings = _settings(timeout, insecure, stream_timeout, "/.well-known/agent-card.json", auth_bearer)
    http = HttpClient(settings)
    try:
        client = JsonRpcClient(http, settings, jsonrpc_url)
        from a2a.types import AgentCard
        # Disable streaming in ping to avoid SSE requirements here
        fake = AgentCard.model_validate({
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
        })
        checks = JsonRpcChecks(fake, jsonrpc_url, client)
        section = checks.run_section()
        reporter.section(section)
        raise typer.Exit(code=reporter.summary_exit_code([section]))
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
):
    """
    Fetch the AgentCard from the target and ping the declared JSON-RPC URL.
    """
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
            raise typer.Exit(code=reporter.summary_exit_code(sections))

        # Determine JSON-RPC URL (same logic as in FullSuite)
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
            from .models import CheckResult, Severity
            sections.append(Section(
                title="JSON-RPC",
                results=[CheckResult(rule="RPC-URL", ok=False, message="Agent does not declare a JSON-RPC interface", severity=Severity.ERROR)]
            ))
            for sec in sections:
                reporter.section(sec)
            raise typer.Exit(code=1)

        client = JsonRpcClient(http, settings, jsonrpc_url)
        from a2a.types import AgentCard as _AC
        # Disable streaming in ping to avoid SSE requirements here
        fake = _AC.model_validate({
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
        })
        checks = JsonRpcChecks(fake, jsonrpc_url, client)
        sections.append(checks.run_section())
        for sec in sections:
            reporter.section(sec)
        raise typer.Exit(code=reporter.summary_exit_code(sections))
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
):
    console = Console()
    reporter = Reporter(console)
    settings = _settings(timeout, insecure, stream_timeout, "/.well-known/agent-card.json", auth_bearer)
    http = HttpClient(settings)
    try:
        client = JsonRpcClient(http, settings, jsonrpc_url)
        try:
            payload, events = client.stream_text(text)
            ok = len(events) > 0
            from .models import CheckResult, Section, Severity
            section = Section(title="JSON-RPC Stream", results=[
                CheckResult(rule="RPC-STREAM", ok=ok, message="received SSE events" if ok else "no SSE events", severity=Severity.ERROR if not ok else Severity.INFO)
            ])
        except Exception as e:
            from .models import CheckResult, Section, Severity
            section = Section(title="JSON-RPC Stream", results=[
                CheckResult(rule="RPC-STREAM", ok=False, message=f"stream error {e}", severity=Severity.ERROR)
            ])
        reporter.section(section)
        raise typer.Exit(code=reporter.summary_exit_code([section]))
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
):
    console = Console()
    reporter = Reporter(console)
    settings = _settings(timeout, insecure, stream_timeout, well_known_path, auth_bearer)
    suite = FullSuite(settings)
    sections = suite.run(target, card_url)
    for sec in sections:
        reporter.section(sec)
    raise typer.Exit(code=reporter.summary_exit_code(sections))


# --- fixed dummy launcher with --wrong flag ---
@app.command("start_dummy")
def start_dummy(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address for the dummy A2A server"),
    port: int = typer.Option(9999, "--port", help="Port for the dummy A2A server"),
    wrong: bool = typer.Option(False, "--wrong", help="Serve intentionally broken Card/RPC for demo failures"),
):
    """
    Start the built-in Hello-World A2A server (JSON-RPC + SSE).
    """
    hello_main(host=host, port=port, wrong=wrong)


app.add_typer(net_app, name="net")
app.add_typer(card_app, name="card")
app.add_typer(rpc_app, name="rpc")
app.add_typer(suite_app, name="suite")

if __name__ == "__main__":
    app()
