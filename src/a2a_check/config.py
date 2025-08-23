from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Mapping


@dataclass(frozen=True)
class Settings:
    """Immutable configuration for network and protocol operations."""

    timeout_s: float = 8.0
    verify_tls: bool = True
    stream_timeout_s: float = 12.0
    well_known_path: str = "/.well-known/agent-card.json"
    auth_bearer: Optional[str] = None
    extra_headers: Optional[Mapping[str, str]] = None
