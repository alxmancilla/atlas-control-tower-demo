from __future__ import annotations

import hmac
from collections.abc import Mapping

from fastapi import Request
from fastapi.responses import JSONResponse

from .config import Settings


def _configured_hosts(settings: Settings) -> set[str]:
    names = set(settings.allowed_hosts)
    if settings.host not in {"0.0.0.0", "::", "localhost", "127.0.0.1", "::1"}:
        names.add(f"[{settings.host}]" if ":" in settings.host and not settings.host.startswith("[") else settings.host)
    return {f"{name}:{settings.port}" for name in names}


def dashboard_security_headers(nonce: str) -> Mapping[str, str]:
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }


async def guard_write_request(request: Request, call_next):
    settings: Settings = request.app.state.settings
    host = request.headers.get("host", "").lower()
    if host not in _configured_hosts(settings):
        return JSONResponse(status_code=421, content={"detail": f'Host "{host}" is not served by this demo'})
    if request.method not in {"POST", "GET", "HEAD"}:
        return JSONResponse(status_code=405, content={"detail": f"Method {request.method} is not allowed"})
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin.lower() != f"http://{host}":
            return JSONResponse(status_code=403, content={"detail": "Cross-origin requests are not allowed"})
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return JSONResponse(status_code=415, content={"detail": "POST requests must use Content-Type: application/json"})
        if settings.admin_token and not hmac.compare_digest(request.headers.get("x-admin-token", ""), settings.admin_token):
            return JSONResponse(status_code=401, content={"detail": "Admin token required for changes"})
    return await call_next(request)