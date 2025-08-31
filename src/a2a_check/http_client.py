from __future__ import annotations
from typing import Any, Dict
import httpx
from httpx_sse import connect_sse
from .config import Settings



class HttpClient:
    """Synchronous HTTP client wrapper for JSON and SSE."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        headers = {}
        if settings.auth_bearer:
            headers["Authorization"] = f"Bearer {settings.auth_bearer}"
        if settings.extra_headers:
            headers.update(dict(settings.extra_headers))
        self._client = httpx.Client(
            timeout=settings.timeout_s,
            follow_redirects=True,
            headers=headers,
            verify=settings.verify_tls,
        )

    def get(self, url: str) -> httpx.Response:
        return self._client.get(url)

    def post_json(self, url: str, payload: Dict[str, Any]) -> httpx.Response:
        return self._client.post(url, json=payload)

    def sse_post(self, url: str, payload: Dict[str, Any]):
        return connect_sse(self._client, "POST", url, json=payload)

    def close(self) -> None:
        self._client.close()
