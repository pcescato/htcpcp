"""
HTCPCP/1.0 — Raw TCP Server
Bypasses uvicorn/h11 entirely to handle custom HTTP methods.

Why raw TCP? Because uvicorn validates HTTP methods at the socket level,
before h11 even sees the request. Since BREW, WHEN, PROPFIND are not
registered IANA methods, they get rejected upstream of any patch.

This server implements a minimal HTTP/1.1 parser that accepts any
method that is a valid RFC 7230 token — which BREW, WHEN, PROPFIND are.

Usage:
    python server.py
    # Listens on port 2324
"""

import asyncio
import json
import re
import sys
from urllib.parse import urlparse

from models import (
    DECAF_RESPONSE,
    SUPPORTED_ADDITIONS,
    PotStatus,
    PotType,
    get_pot,
)

import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger()

HOST = "127.0.0.1"
PORT = 2324


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def http_response(status: int, body: dict, extra_headers: dict = None) -> bytes:
    status_texts = {
        200: "OK",
        404: "Not Found",
        405: "Method Not Allowed",
        406: "Not Acceptable",
        418: "I'm a Teapot",
        503: "Service Unavailable",
    }
    body_bytes = json.dumps(body, indent=2).encode()
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body_bytes)),
        "X-Protocol": "HTCPCP/1.0",
        "X-RFC": "RFC-2324, RFC-7168",
        "X-Powered-By": "Coffee",
        "Connection": "close",
        **(extra_headers or {}),
    }
    header_lines = "\r\n".join(f"{k}: {v}" for k, v in headers.items())
    status_text = status_texts.get(status, "Unknown")
    head = f"HTTP/1.1 {status} {status_text}\r\n{header_lines}\r\n\r\n"
    return head.encode() + body_bytes


def parse_request(raw: bytes) -> tuple[str, str, dict, bytes] | None:
    """
    Parse a raw HTTP request.
    Returns (method, path, headers, body) or None if malformed.
    """
    try:
        # Split head from body on double CRLF
        if b"\r\n\r\n" in raw:
            head, body = raw.split(b"\r\n\r\n", 1)
        else:
            head, body = raw, b""

        lines = head.decode(errors="replace").split("\r\n")
        request_line = lines[0]
        parts = request_line.split(" ")
        if len(parts) < 2:
            return None

        method = parts[0].upper()
        path = parts[1]

        headers = {}
        for line in lines[1:]:
            if ": " in line:
                k, v = line.split(": ", 1)
                headers[k.lower()] = v.strip()

        return method, path, headers, body
    except Exception:
        return None


def parse_additions(header: str | None) -> dict:
    if not header:
        return {}
    result = {}
    for part in header.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result


# ── Route handlers ────────────────────────────────────────────────────────────

def handle_brew(pot_id: str, headers: dict) -> bytes:
    pot = get_pot(pot_id)
    if not pot:
        return http_response(404, {"error": "Not Found", "pot_id": pot_id})

    # RFC 2324 §2.3.2 — teapot → 418, non-negotiable
    if pot.pot_type == PotType.TEAPOT:
        log.warning("htcpcp.teapot_detected", pot_id=pot_id, status_code=418)
        return http_response(418, {
            "status": 418,
            "error": "I'm a teapot",
            "body": "The requested entity body is short and stout.",
            "hint": "Tip me over and pour me out.",
            "pot_id": pot_id,
            "rfc": "RFC 2324 §2.3.2",
            "suggestion": "Try coffee://pot-1 instead.",
        })

    if pot.level == 0:
        return http_response(503, {
            "error": "Service Unavailable",
            "message": "Pot is empty. Refill required.",
            "note": "This is 503, not 418. The pot is a coffee pot — it's just empty.",
        })

    additions = parse_additions(headers.get("accept-additions"))

    # Decaf check — RFC 2324 §2.1.1
    if "decaf" in additions:
        log.warning("htcpcp.decaf_refused", additions=additions)
        return http_response(406, {
            "error": "Not Acceptable",
            "message": "Decaffeinated coffee? What's the point?",
            "rfc": "RFC 2324 §2.1.1",
        })

    # Validate additions
    unsupported = [
        f"{k}={v}" for k, v in additions.items()
        if k in SUPPORTED_ADDITIONS and v not in SUPPORTED_ADDITIONS[k]
    ]
    if unsupported:
        return http_response(406, {
            "error": "Not Acceptable",
            "unsupported_additions": unsupported,
            "hint": "Use PROPFIND /coffee/{pot_id}/additions to list valid values.",
        })

    record = pot.add_brew(additions)
    pot.level -= 1
    has_milk = "milk-type" in additions
    pot.status = PotStatus.POURING_MILK if has_milk else PotStatus.BREWING

    log.info("htcpcp.brew",
        pot_id=pot_id, brew_id=record.id,
        additions=additions, milk_pouring=has_milk, status_code=200,
    )

    return http_response(200, {
        "brew_id": record.id,
        "message": "Coffee is brewing.",
        "pot": pot_id,
        "accept-additions": additions,
        "milk_pouring": has_milk,
        "when_required": has_milk,
        "protocol": "HTCPCP/1.0",
    })


def handle_get_status(pot_id: str) -> bytes:
    pot = get_pot(pot_id)
    if not pot:
        return http_response(404, {"error": "Not Found", "pot_id": pot_id})
    log.info("htcpcp.get_status", pot_id=pot_id, status=pot.status)
    return http_response(200, pot.to_dict())


def handle_get_history(pot_id: str) -> bytes:
    pot = get_pot(pot_id)
    if not pot:
        return http_response(404, {"error": "Not Found", "pot_id": pot_id})
    return http_response(200, {
        "pot_id": pot_id,
        "total_brews": len(pot.brew_history),
        "brews": [r.to_dict() for r in pot.brew_history],
    })


def handle_propfind(pot_id: str) -> bytes:
    pot = get_pot(pot_id)
    if not pot:
        return http_response(404, {"error": "Not Found", "pot_id": pot_id})
    log.info("htcpcp.propfind", pot_id=pot_id)
    return http_response(200, {
        **SUPPORTED_ADDITIONS,
        "decaf": DECAF_RESPONSE,
        "rfc": "RFC 2324 §2.1.1",
    })


def handle_when(pot_id: str) -> bytes:
    pot = get_pot(pot_id)
    if not pot:
        return http_response(404, {"error": "Not Found", "pot_id": pot_id})

    if pot.status != PotStatus.POURING_MILK:
        log.info("htcpcp.when_noop", pot_id=pot_id, status=pot.status)
        return http_response(200, {
            "message": "WHEN acknowledged.",
            "note": "No milk was being poured, but your enthusiasm is appreciated.",
            "current_status": pot.status,
            "rfc": "RFC 2324 §2.1.3",
        })

    pot.status = PotStatus.BREWING
    log.info("htcpcp.when_milk_stopped", pot_id=pot_id)
    return http_response(200, {
        "message": "Milk pouring stopped.",
        "detail": "The server has acknowledged WHEN and stopped the milk stream.",
        "current_status": pot.status,
        "protocol": "HTCPCP/1.0",
        "rfc": "RFC 2324 §2.1.3",
    })


def handle_registry() -> bytes:
    from models import POT_REGISTRY
    return http_response(200, {
        "protocol": "HTCPCP/1.0",
        "rfc": ["RFC 2324", "RFC 7168"],
        "pots": {uri: pot.to_dict() for uri, pot in POT_REGISTRY.items()},
        "methods": ["BREW", "GET", "PROPFIND", "WHEN"],
    })


# ── Router ────────────────────────────────────────────────────────────────────

# Path patterns → handler
_ROUTES = [
    (re.compile(r"^/coffee/([^/]+)$"),           {"BREW": handle_brew, "POST": handle_brew}),
    (re.compile(r"^/coffee/([^/]+)/status$"),     {"GET": handle_get_status}),
    (re.compile(r"^/coffee/([^/]+)/history$"),    {"GET": handle_get_history}),
    (re.compile(r"^/coffee/([^/]+)/additions$"),  {"PROPFIND": handle_propfind}),
    (re.compile(r"^/coffee/([^/]+)/stop-milk$"),  {"WHEN": handle_when}),
    (re.compile(r"^/$"),                          {"GET": lambda _: handle_registry()}),
]


def dispatch(method: str, path: str, headers: dict) -> bytes:
    # BREW on wrong route → 418 (middleware logic)
    if method == "BREW" and not path.startswith("/coffee/"):
        return http_response(418, {
            "error": "Wrong universe",
            "message": f"BREW is not valid on {path}",
            "hint": "BREW is only valid on coffee:// URIs",
            "rfc": "RFC 2324 §2.1",
        })

    for pattern, method_map in _ROUTES:
        m = pattern.match(path)
        if m:
            groups = m.groups()
            pot_id = groups[0] if groups else None
            handler = method_map.get(method)
            if handler is None:
                return http_response(405, {
                    "error": "Method Not Allowed",
                    "allowed": list(method_map.keys()),
                })
            return handler(pot_id) if pot_id else handler()

    return http_response(404, {"error": "Not Found", "path": path})


# ── Async TCP server ──────────────────────────────────────────────────────────

async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        raw = await asyncio.wait_for(reader.read(8192), timeout=5.0)
        if not raw:
            return

        parsed = parse_request(raw)
        if not parsed:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            return

        method, path, headers, body = parsed
        response = dispatch(method, path, headers)
        writer.write(response)
        await writer.drain()

    except asyncio.TimeoutError:
        pass
    except Exception as e:
        log.error("htcpcp.server_error", error=str(e))
    finally:
        writer.close()


async def main():
    server = await asyncio.start_server(handle_connection, HOST, PORT)
    log.info("htcpcp.startup",
        protocol="HTCPCP/1.0",
        rfc=["RFC-2324", "RFC-7168"],
        host=HOST,
        port=PORT,
        note="Raw TCP server — custom methods fully supported",
    )
    print(f"\n☕  HTCPCP/1.0 listening on {HOST}:{PORT}\n")
    print(f"    curl -X BREW http://{HOST}:{PORT}/coffee/pot-1 \\")
    print(f'         -H "Accept-Additions: milk-type=Whole-milk; alcohol-type=Whisky"\n')
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
