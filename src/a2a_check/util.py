from __future__ import annotations
from urllib.parse import urlparse


def ensure_scheme(u: str) -> str:
    """Ensure an explicit scheme is present."""
    return u if "://" in u else f"http://{u}"


def build_origin(u: str) -> str:
    """Return scheme://host[:port] for a given input."""
    p = urlparse(ensure_scheme(u))
    scheme = p.scheme or "http"
    netloc = p.netloc or p.path.split("/")[0]
    return f"{scheme}://{netloc}"


def resolve_card_url(target: str, override_card_url: str | None, well_known_path: str) -> str:
    """Resolve the AgentCard URL from a base target or an explicit override."""
    if override_card_url:
        return ensure_scheme(override_card_url)
    t = ensure_scheme(target)
    if "/.well-known/agent-card.json" in t or t.endswith("agent-card.json"):
        return t
    origin = build_origin(t)
    wp = well_known_path if well_known_path.startswith("/") else f"/{well_known_path}"
    return f"{origin}{wp}"
