"""
HTCPCP/1.0 — Raw TCP Server
RFC 2324 (coffee) + RFC 7168 (tea)

Usage:
    python server.py
"""

import asyncio
import json
import re
import structlog

from models import (
    DECAF_RESPONSE,
    SUPPORTED_ADDITIONS,
    PotStatus,
    PotType,
    get_pot,
)

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

STATUS_TEXTS = {
    200: "OK",
    404: "Not Found",
    405: "Method Not Allowed",
    406: "Not Acceptable",
    418: "I'm a Teapot",
    503: "Service Unavailable",
}

def http_response(status: int, body: dict) -> bytes:
    body_bytes = json.dumps(body, indent=2).encode("utf-8")
    status_text = STATUS_TEXTS.get(status, "Unknown")
    headers = (
        f"HTTP/1.1 {status} {status_text}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        f"X-Protocol: HTCPCP/1.0\r\n"
        f"X-RFC: RFC-2324, RFC-7168\r\n"
        f"X-Powered-By: Coffee\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )
    return headers.encode("utf-8") + body_bytes


async def read_request(reader: asyncio.StreamReader) -> bytes:
    """
    Read until we have the full HTTP head (double CRLF).
    Then read Content-Length bytes of body if present.
    """
    raw = b""
    # Read until \r\n\r\n
    while b"\r\n\r\n" not in raw:
        chunk = await asyncio.wait_for(reader.read(4096), timeout=5.0)
        if not chunk:
            break
        raw += chunk

    # Read body if Content-Length is set
    head = raw.split(b"\r\n\r\n")[0].decode(errors="replace")
    for line in head.split("\r\n")[1:]:
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
            if length > 0:
                body = await asyncio.wait_for(reader.read(length), timeout=5.0)
                raw += body
            break

    return raw


def parse_request(raw: bytes):
    """Returns (method, path, headers, body) or None."""
    try:
        if b"\r\n\r\n" in raw:
            head_bytes, body = raw.split(b"\r\n\r\n", 1)
        else:
            head_bytes, body = raw, b""

        lines = head_bytes.decode(errors="replace").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) < 2:
            return None

        method = parts[0].upper()
        path   = parts[1].split("?")[0]  # strip query string

        headers = {}
        for line in lines[1:]:
            if ": " in line:
                k, v = line.split(": ", 1)
                headers[k.lower()] = v.strip()

        return method, path, headers, body
    except Exception as e:
        log.error("htcpcp.parse_error", error=str(e))
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


# ── Handlers ──────────────────────────────────────────────────────────────────

def handle_brew(pot_id: str, headers: dict) -> bytes:
    pot = get_pot(pot_id)
    if not pot:
        return http_response(404, {"error": "Not Found", "pot_id": pot_id})

    if pot.pot_type == PotType.TEAPOT:
        log.warning("htcpcp.teapot_detected", pot_id=pot_id)
        return http_response(418, {
            "status": 418,
            "error": "I'm a teapot",
            "body": "The requested entity body is short and stout.",
            "hint": "Tip me over and pour me out.",
            "pot_id": pot_id,
            "rfc": "RFC 2324 §2.3.2",
            "suggestion": "Try /coffee/pot-1 instead.",
        })

    if pot.level == 0:
        return http_response(503, {
            "error": "Service Unavailable",
            "message": "Pot is empty. Refill required.",
            "note": "503, not 418 — the pot is a coffee pot, just empty.",
        })

    additions = parse_additions(headers.get("accept-additions"))

    if "decaf" in additions:
        log.warning("htcpcp.decaf_refused")
        return http_response(406, {
            "error": "Not Acceptable",
            "message": "Decaffeinated coffee? What's the point?",
            "rfc": "RFC 2324 §2.1.1",
        })

    unsupported = [
        f"{k}={v}" for k, v in additions.items()
        if k in SUPPORTED_ADDITIONS and v not in SUPPORTED_ADDITIONS[k]
    ]
    if unsupported:
        return http_response(406, {
            "error": "Not Acceptable",
            "unsupported_additions": unsupported,
        })

    record  = pot.add_brew(additions)
    pot.level -= 1
    has_milk = "milk-type" in additions
    pot.status = PotStatus.POURING_MILK if has_milk else PotStatus.BREWING

    log.info("htcpcp.brew",
        pot_id=pot_id, brew_id=record.id,
        additions=additions, milk_pouring=has_milk,
    )
    return http_response(200, {
        "brew_id":        record.id,
        "message":        "Coffee is brewing.",
        "pot":            pot_id,
        "accept-additions": additions,
        "milk_pouring":   has_milk,
        "when_required":  has_milk,
        "protocol":       "HTCPCP/1.0",
    })


def handle_get_status(pot_id: str, _headers: dict) -> bytes:
    pot = get_pot(pot_id)
    if not pot:
        return http_response(404, {"error": "Not Found", "pot_id": pot_id})
    return http_response(200, pot.to_dict())


def handle_get_history(pot_id: str, _headers: dict) -> bytes:
    pot = get_pot(pot_id)
    if not pot:
        return http_response(404, {"error": "Not Found", "pot_id": pot_id})
    return http_response(200, {
        "pot_id":      pot_id,
        "total_brews": len(pot.brew_history),
        "brews":       [r.to_dict() for r in pot.brew_history],
    })


def handle_propfind(pot_id: str, _headers: dict) -> bytes:
    pot = get_pot(pot_id)
    if not pot:
        return http_response(404, {"error": "Not Found", "pot_id": pot_id})
    return http_response(200, {
        **SUPPORTED_ADDITIONS,
        "decaf": DECAF_RESPONSE,
        "rfc": "RFC 2324 §2.1.1",
    })


def handle_when(pot_id: str, _headers: dict) -> bytes:
    pot = get_pot(pot_id)
    if not pot:
        return http_response(404, {"error": "Not Found", "pot_id": pot_id})

    if pot.status != PotStatus.POURING_MILK:
        return http_response(200, {
            "message": "WHEN acknowledged.",
            "note":    "No milk was being poured, but your enthusiasm is appreciated.",
            "rfc":     "RFC 2324 §2.1.3",
        })

    pot.status = PotStatus.BREWING
    log.info("htcpcp.when_milk_stopped", pot_id=pot_id)
    return http_response(200, {
        "message":        "Milk pouring stopped.",
        "detail":         "The server acknowledged WHEN and stopped the milk stream.",
        "current_status": pot.status,
        "rfc":            "RFC 2324 §2.1.3",
    })


def handle_registry(_headers: dict) -> bytes:
    from models import POT_REGISTRY
    return http_response(200, {
        "protocol": "HTCPCP/1.0",
        "rfc":      ["RFC 2324", "RFC 7168"],
        "pots":     {uri: pot.to_dict() for uri, pot in POT_REGISTRY.items()},
        "methods":  ["BREW", "GET", "PROPFIND", "WHEN"],
    })


# ── Router ────────────────────────────────────────────────────────────────────

ROUTES = [
    (re.compile(r"^/coffee/([^/]+)$"),           {"BREW": handle_brew, "POST": handle_brew}),
    (re.compile(r"^/coffee/([^/]+)/status$"),     {"GET":  handle_get_status}),
    (re.compile(r"^/coffee/([^/]+)/history$"),    {"GET":  handle_get_history}),
    (re.compile(r"^/coffee/([^/]+)/additions$"),  {"PROPFIND": handle_propfind}),
    (re.compile(r"^/coffee/([^/]+)/stop-milk$"),  {"WHEN": handle_when}),
    (re.compile(r"^/$"),                          {"GET":  lambda _id, h: handle_registry(h)}),
]


def dispatch(method: str, path: str, headers: dict) -> bytes:
    if method == "BREW" and not path.startswith("/coffee/"):
        return http_response(418, {
            "error": "Wrong universe",
            "hint":  "BREW is only valid on /coffee/{pot_id}",
            "rfc":   "RFC 2324 §2.1",
        })

    for pattern, method_map in ROUTES:
        m = pattern.match(path)
        if m:
            pot_id  = m.group(1) if m.lastindex else None
            handler = method_map.get(method)
            if handler is None:
                return http_response(405, {
                    "error":   "Method Not Allowed",
                    "allowed": list(method_map.keys()),
                })
            return handler(pot_id, headers)

    return http_response(404, {"error": "Not Found", "path": path})


# ── TCP server ────────────────────────────────────────────────────────────────

async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    peer = writer.get_extra_info("peername")
    try:
        raw = await read_request(reader)
        if not raw:
            return

        parsed = parse_request(raw)
        if not parsed:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return

        method, path, headers, body = parsed
        log.info("htcpcp.request", method=method, path=path, peer=str(peer))

        response = dispatch(method, path, headers)
        writer.write(response)
        await writer.drain()

    except asyncio.TimeoutError:
        log.warning("htcpcp.timeout", peer=str(peer))
    except Exception as e:
        log.error("htcpcp.error", error=str(e), peer=str(peer))
        try:
            writer.write(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
        except Exception:
            pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(handle_connection, HOST, PORT)
    log.info("htcpcp.startup",
        protocol="HTCPCP/1.0",
        host=HOST, port=PORT,
    )
    print(f"\n☕  HTCPCP/1.0 — RFC 2324  ({HOST}:{PORT})\n")
    print(f"    curl -X BREW http://{HOST}:{PORT}/coffee/pot-1 \\")
    print(f'         -H "Accept-Additions: milk-type=Whole-milk; alcohol-type=Whisky"')
    print(f"\n    curl http://{HOST}:{PORT}/\n")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
