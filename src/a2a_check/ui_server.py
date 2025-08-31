# src/a2a_check/ui_server.py
from __future__ import annotations
from typing import Optional, Dict, Iterable
from contextlib import asynccontextmanager
import importlib.resources as res
import logging
import time
import webbrowser

import httpx
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response, StreamingResponse, JSONResponse, FileResponse
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException
import uvicorn

from .config import Settings
from .http_client import HttpClient
from .card_service import CardService


# ---------- Logging ----------
log = logging.getLogger("a2a.ui")
if not log.handlers:
    # Falls uvicorn keine Handler gesetzt hat
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _mask_token(tok: Optional[str]) -> str:
    if not tok:
        return "-"
    t = tok.strip()
    if len(t) <= 8:
        return "***"
    return f"{t[:4]}…{t[-4:]}"


# ---------- Helpers ----------
def _drop_hop_by_hop(headers: Dict[str, str]) -> Dict[str, str]:
    hop = {
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
    }
    return {k: v for k, v in headers.items() if k.lower() not in hop}


def _is_sse(request: Request) -> bool:
    accept = request.headers.get("accept", "").lower()
    path = request.url.path
    return (
        "text/event-stream" in accept
        or path.endswith(":stream")
        or path.endswith(":subscribe")
    )


async def _stream_upstream(resp: httpx.Response) -> Iterable[bytes]:
    async for chunk in resp.aiter_raw():
        yield chunk


def _pick_url_for_transport(card: object, transport: str) -> Optional[str]:
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


# ---------- App ----------
def run_ui(
    rest_base: Optional[str],
    auth_bearer: Optional[str],
    host: str,
    port: int,
    verify_tls: bool,
    timeout_s: float,
    stream_timeout_s: float,
    well_known_path: str,
    open_browser: bool = True,
) -> None:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = Settings(
            timeout_s=timeout_s,
            verify_tls=verify_tls,
            stream_timeout_s=stream_timeout_s,
            well_known_path=well_known_path,
            auth_bearer=auth_bearer,
        )
        app.state.rest_base = rest_base
        app.state.client = httpx.AsyncClient(verify=verify_tls, timeout=timeout_s)
        log.info(
            "UI start | rest_base=%s | verify_tls=%s | timeout_s=%.1f | stream_timeout_s=%.1f | well_known=%s | auth=%s",
            rest_base or "-", verify_tls, timeout_s, stream_timeout_s, well_known_path, _mask_token(auth_bearer)
        )
        try:
            yield
        finally:
            await app.state.client.aclose()
            log.info("UI shutdown: httpx client closed")

    app = FastAPI(lifespan=lifespan)

    # ------- Static SPA -------
    static_root = res.files("a2a_check").joinpath("ui_static")
    app.mount("/assets", StaticFiles(directory=str(static_root.joinpath("assets"))), name="assets")

    @app.get("/", include_in_schema=False)
    async def index_root():
        return FileResponse(str(static_root.joinpath("index.html")))

    # SPA history fallback
    @app.exception_handler(StarletteHTTPException)
    async def spa_fallback(request: Request, exc: StarletteHTTPException):
        path = request.url.path
        if (
            exc.status_code == 404
            and request.method == "GET"
            and "text/html" in request.headers.get("accept", "")
            and not path.startswith(("/api", "/control", "/assets", "/favicon.ico"))
        ):
            log.debug("SPA fallback -> index.html for path=%s", path)
            return FileResponse(str(static_root.joinpath("index.html")))
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    # ------- Control endpoints -------
    @app.get("/control/config")
    async def get_config(request: Request):
        s: Settings = request.app.state.settings
        rb = request.app.state.rest_base
        log.info("CONTROL get_config | rest_base=%s | auth=%s | verify_tls=%s", rb or "-", _mask_token(s.auth_bearer), s.verify_tls)
        return {
            "restBase": rb,
            "authBearerSet": bool(s.auth_bearer),
            "verifyTLS": s.verify_tls,
            "wellKnownPath": s.well_known_path,
        }

    @app.get("/control/card")
    async def get_card(url: str, request: Request):
        s: Settings = request.app.state.settings
        log.info("CONTROL get_card | url=%s", url)
        http = HttpClient(s)
        try:
            t0 = time.perf_counter()
            _, raw, _ = CardService(http, s).fetch_raw(url, None)
            dt = (time.perf_counter() - t0) * 1000
            log.info("CONTROL get_card OK | url=%s | took=%.1fms", url, dt)
            return raw
        except Exception as e:
            log.exception("CONTROL get_card FAIL | url=%s | err=%s", url, e)
            return JSONResponse({"error": str(e)}, status_code=502)
        finally:
            http.close()

    @app.get("/control/resolve")
    async def resolve_rest_base(cardUrl: Optional[str] = None, target: Optional[str] = None, request: Request = None):
        tgt = cardUrl or target
        if not tgt:
            return JSONResponse({"error": "cardUrl or target required"}, status_code=400)
        s: Settings = request.app.state.settings
        log.info("CONTROL resolve | target=%s", tgt)
        http = HttpClient(s)
        try:
            cs = CardService(http, s)
            _, raw, _ = cs.fetch_raw(tgt, None)
            card, _ = cs.parse(raw)
            if not card:
                log.warning("CONTROL resolve FAIL | parse error")
                return JSONResponse({"error": "AgentCard parse failed"}, status_code=400)
            rest = _pick_url_for_transport(card, "HTTP+JSON")
            if not rest:
                log.warning("CONTROL resolve FAIL | no HTTP+JSON declared")
                return JSONResponse({"error": "Agent declares no HTTP+JSON interface"}, status_code=400)
            log.info("CONTROL resolve OK | rest_base=%s", rest)
            return {"restBase": rest}
        except Exception as e:
            log.exception("CONTROL resolve EXC | err=%s", e)
            return JSONResponse({"error": str(e)}, status_code=502)
        finally:
            http.close()

    @app.post("/control/config")
    async def set_config(request: Request):
        body = await request.json()
        s: Settings = request.app.state.settings
        log.info("CONTROL set_config | has_restBase=%s | has_cardUrl=%s | auth=%s",
                 bool(body.get("restBase")), bool(body.get("cardUrl") or body.get("target")), _mask_token(body.get("authBearer")))

        # authBearer optional aktualisieren
        ab = body.get("authBearer")
        if isinstance(ab, str):
            request.app.state.settings = Settings(
                timeout_s=s.timeout_s,
                verify_tls=s.verify_tls,
                stream_timeout_s=s.stream_timeout_s,
                well_known_path=s.well_known_path,
                auth_bearer=ab.strip() or None,
                extra_headers=s.extra_headers,
            )
            s = request.app.state.settings
            log.info("CONTROL set_config | auth updated -> %s", _mask_token(s.auth_bearer))

        # restBase bestimmen; Card an UI zurückgeben
        rest = body.get("restBase")
        card_json = None
        if not rest:
            tgt = body.get("cardUrl") or body.get("target")
            if not tgt:
                return JSONResponse({"error": "Provide restBase or cardUrl"}, status_code=400)
            http = HttpClient(s)
            try:
                t0 = time.perf_counter()
                cs = CardService(http, s)
                _, raw, _ = cs.fetch_raw(tgt, None)
                card_json = raw
                card, _ = cs.parse(raw)
                if not card:
                    log.warning("CONTROL set_config | parse failed")
                    return JSONResponse({"error": "AgentCard parse failed"}, status_code=400)
                rest = _pick_url_for_transport(card, "HTTP+JSON")
                if not rest:
                    log.warning("CONTROL set_config | no HTTP+JSON")
                    return JSONResponse({"error": "Agent declares no HTTP+JSON interface"}, status_code=400)
                log.info("CONTROL set_config OK | resolved rest_base=%s | took=%.1fms", rest, (time.perf_counter() - t0) * 1000)
            except Exception as e:
                log.exception("CONTROL set_config EXC | err=%s", e)
                return JSONResponse({"error": str(e)}, status_code=502)
            finally:
                http.close()

        request.app.state.rest_base = rest
        return {"restBase": rest, "configured": True, "card": card_json}

    # ------- Reverse Proxy (/api) -------
    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request):
        client: httpx.AsyncClient = request.app.state.client
        s: Settings = request.app.state.settings
        rest = request.app.state.rest_base
        if not rest:
            log.warning("PROXY blocked | rest_base not configured")
            return JSONResponse({"error": "Proxy not configured. POST /control/config first."}, status_code=400)

        url = f"{rest.rstrip('/')}/{path}"
        body = await request.body()
        headers = _drop_hop_by_hop(dict(request.headers))

        # Server-seitiges Authorization-Forwarding
        if s.auth_bearer:
            headers["authorization"] = s.auth_bearer if s.auth_bearer.lower().startswith("bearer ") else f"Bearer {s.auth_bearer}"

        t0 = time.perf_counter()
        try:
            if _is_sse(request):
                log.info("PROXY SSE → %s %s -> %s", request.method, request.url.path, url)
                cm = client.stream(
                    request.method,
                    url,
                    headers=headers,
                    content=body if request.method != "GET" else None,
                    params=request.query_params,
                )
                resp = await cm.__aenter__()
                ct = resp.headers.get("content-type") or "text/event-stream"
                # Start-Log; Ende wird vom BackgroundTask geloggt
                log.info("PROXY SSE upstream %s | status=%s | started in %.1fms",
                         url, resp.status_code, (time.perf_counter() - t0) * 1000)

                async def _close(cm_):
                    try:
                        await cm_.__aexit__(None, None, None)
                        log.info("PROXY SSE closed | %s", url)
                    except Exception as e:
                        log.exception("PROXY SSE close error | %s | %s", url, e)

                return StreamingResponse(
                    _stream_upstream(resp),
                    media_type=ct,
                    status_code=resp.status_code,
                    background=BackgroundTask(_close, cm),
                )

            # Normale Requests
            log.info("PROXY → %s %s -> %s", request.method, request.url.path, url)
            r = await client.request(
                request.method,
                url,
                headers=headers,
                content=body,
                params=request.query_params,
            )
            dt = (time.perf_counter() - t0) * 1000
            log.info("PROXY ← %s | status=%s | %.1fms | ct=%s",
                     url, r.status_code, dt, r.headers.get("content-type"))
            return Response(
                content=r.content,
                status_code=r.status_code,
                headers={
                    "content-type": r.headers.get("content-type", ""),
                    "x-a2a-proxy-upstream": url,
                },
            )
        except Exception as e:
            log.exception("PROXY EXC | %s %s -> %s | err=%s", request.method, request.url.path, url, e)
            return JSONResponse({"error": str(e)}, status_code=502)

    if open_browser:
        webbrowser.open(f"http://{host}:{port}")

    uvicorn.run(app, host=host, port=port)
