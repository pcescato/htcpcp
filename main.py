"""
HTCPCP/1.0 — Server
Hyper Text Coffee Pot Control Protocol
RFC 2324 (coffee) · RFC 7168 (tea)

Usage:
    pip install -r requirements.txt
    uvicorn main:app --reload --port 2324

Then:
    curl -X BREW http://localhost:2324/coffee/pot-1 \
      -H "Accept-Additions: milk-type=Whole-milk; alcohol-type=Whisky"
"""

# ── Patch h11 to accept custom HTCPCP methods ─────────────────────────────────
#
# uvicorn uses h11 for HTTP/1.1 parsing. h11 validates method names against
# a strict allowlist — BREW, WHEN, PROPFIND are not on it.
# We extend it before the server starts so custom methods pass through.
#
# Our methods are valid RFC 7230 tokens (uppercase alpha), just not registered.
# The patch is therefore RFC-safe — we're not bypassing anything meaningful.

_HTCPCP_METHODS = {b"BREW", b"WHEN", b"PROPFIND"}

try:
    import h11._readers
    h11._readers.KNOWN_METHODS = (  # type: ignore[attr-defined]
        h11._readers.KNOWN_METHODS | _HTCPCP_METHODS
    )
except Exception:
    pass

try:
    import h11._util

    _orig = h11._util.normalize_method  # type: ignore[attr-defined]

    def _patched(method: bytes) -> bytes:
        if method.upper() in _HTCPCP_METHODS:
            return method.upper()
        return _orig(method)

    h11._util.normalize_method = _patched  # type: ignore[attr-defined]
except Exception:
    pass

# ─────────────────────────────────────────────────────────────────────────────

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from routes import router


# ── Structured logging ────────────────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)

log = structlog.get_logger()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="HTCPCP/1.0",
    description="Hyper Text Coffee Pot Control Protocol — RFC 2324 + RFC 7168",
    version="1.0.0",
    docs_url="/htcpcp-docs",
    redoc_url="/htcpcp-redoc",
)


# ── Middleware ────────────────────────────────────────────────────────────────

class HTCPCPMiddleware(BaseHTTPMiddleware):
    """
    Enforce HTCPCP protocol headers on all responses.
    Also intercepts rogue BREW calls on non-coffee routes.
    """
    async def dispatch(self, request: Request, call_next):
        # Detect a BREW on a non-coffee route
        # A developer confused about which universe they're in deserves a 418
        if request.method == "BREW" and not request.url.path.startswith("/coffee"):
            log.warning("htcpcp.wrong_universe",
                method="BREW",
                path=request.url.path,
                status_code=418,
            )
            return JSONResponse(status_code=418, content={
                "error": "Wrong universe",
                "message": f"BREW is not valid on {request.url.path}",
                "hint": "BREW is only valid on coffee:// URIs — try /coffee/pot-1",
                "rfc": "RFC 2324 §2.1",
            })

        response = await call_next(request)

        # Stamp every response with protocol headers
        response.headers["X-Protocol"] = "HTCPCP/1.0"
        response.headers["X-RFC"] = "RFC-2324, RFC-7168"
        response.headers["X-Powered-By"] = "Coffee"

        return response


app.add_middleware(HTCPCPMiddleware)
app.include_router(router)


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    from models import POT_REGISTRY
    log.info("htcpcp.startup",
        protocol="HTCPCP/1.0",
        rfc=["RFC-2324", "RFC-7168"],
        registered_pots=list(POT_REGISTRY.keys()),
        port=2324,
    )
