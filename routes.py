"""
HTCPCP/1.0 — Routes
Implements: BREW, GET, PROPFIND, WHEN
RFC 2324 (coffee) + RFC 7168 (tea)
"""

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from models import (
    DECAF_RESPONSE,
    SUPPORTED_ADDITIONS,
    CoffeePot,
    PotStatus,
    PotType,
    get_pot,
)

router = APIRouter()
log = structlog.get_logger()


# ── Helpers ───────────────────────────────────────────────────────────────────

def resolve_pot(pot_id: str) -> CoffeePot:
    pot = get_pot(pot_id)
    if not pot:
        raise HTTPException(status_code=404, detail={
            "error": "Not Found",
            "message": f"No pot registered at coffee://{pot_id} or tea://{pot_id}",
            "registered_pots": ["pot-1", "pot-2", "kettle-1", "kettle-2"],
        })
    return pot


def parse_accept_additions(header: str | None) -> dict[str, str]:
    """
    Parse the Accept-Additions header.
    Format: 'milk-type=Whole-milk; syrup-type=Vanilla; alcohol-type=Whisky'
    RFC 2324 §2.1.1
    """
    if not header:
        return {}
    additions = {}
    for part in header.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            additions[key.strip()] = value.strip()
    return additions


def validate_additions(additions: dict) -> None:
    """
    Validate additions against the RFC 2324 §2.1.1 spec.
    Raises 406 for decaf or unsupported values.
    """
    # RFC 2324 §2.1.1 — no decaf. Ever.
    if "decaf" in additions:
        log.warning("htcpcp.decaf_refused", additions=additions)
        raise HTTPException(status_code=406, detail={
            "error": "Not Acceptable",
            "message": "Decaffeinated coffee? What's the point?",
            "rfc": "RFC 2324 §2.1.1",
        })

    unsupported = [
        f"{k}={v}"
        for k, v in additions.items()
        if k in SUPPORTED_ADDITIONS and v not in SUPPORTED_ADDITIONS[k]
    ]
    if unsupported:
        raise HTTPException(status_code=406, detail={
            "error": "Not Acceptable",
            "unsupported_additions": unsupported,
            "hint": "Use PROPFIND to list valid additions.",
        })


# ── BREW ──────────────────────────────────────────────────────────────────────

@router.api_route("/coffee/{pot_id}", methods=["BREW", "POST"])
async def brew(pot_id: str, request: Request):
    """
    BREW — Trigger an infusion.
    RFC 2324 §2.1 — The BREW method.

    Returns:
        200 — Coffee is brewing
        406 — Not Acceptable (decaf, invalid additions)
        418 — I'm a teapot (if the target pot is a teapot)
        503 — Pot is empty
    """
    pot = resolve_pot(pot_id)

    # RFC 2324 §2.3.2 — Any attempt to brew coffee with a teapot
    # MUST return 418. Non-negotiable.
    if pot.pot_type == PotType.TEAPOT:
        log.warning("htcpcp.teapot_detected", pot_id=pot_id, status_code=418)
        return JSONResponse(status_code=418, content={
            "status": 418,
            "error": "I'm a teapot",
            "body": "The requested entity body is short and stout.",
            "hint": "Tip me over and pour me out.",
            "pot_id": pot_id,
            "pot_type": "teapot",
            "rfc": "RFC 2324 §2.3.2",
            "suggestion": "Try coffee://pot-1 instead.",
        })

    if pot.level == 0:
        log.warning("htcpcp.pot_empty", pot_id=pot_id)
        raise HTTPException(status_code=503, detail={
            "error": "Service Unavailable",
            "message": "Pot is empty. Please refill before brewing.",
            "note": "This is a 503, not a 418. The pot is a coffee pot — it's just empty.",
        })

    additions_header = request.headers.get("accept-additions")
    additions = parse_accept_additions(additions_header)
    validate_additions(additions)

    record = pot.add_brew(additions)
    pot.level -= 1

    # If milk is requested → enter POURING_MILK state
    # Client must send WHEN to exit this state
    has_milk = "milk-type" in additions
    pot.status = PotStatus.POURING_MILK if has_milk else PotStatus.BREWING

    log.info("htcpcp.brew",
        pot_id=pot_id,
        brew_id=record.id,
        additions=additions,
        milk_pouring=has_milk,
        status_code=200,
        protocol="HTCPCP/1.0",
    )

    return JSONResponse(status_code=200, content={
        "brew_id": record.id,
        "message": "Coffee is brewing.",
        "pot": pot_id,
        "accept-additions": additions,
        "milk_pouring": has_milk,
        "when_required": has_milk,
        "protocol": "HTCPCP/1.0",
    })


# ── GET ───────────────────────────────────────────────────────────────────────

@router.get("/coffee/{pot_id}/status")
def get_status(pot_id: str):
    """
    GET — Return the current state of a coffee pot.
    RFC 2324 §2.1.2
    """
    pot = resolve_pot(pot_id)
    log.info("htcpcp.get_status", pot_id=pot_id, status=pot.status)
    return pot.to_dict()


@router.get("/coffee/{pot_id}/history")
def get_history(pot_id: str):
    """Return the brew history for a pot."""
    pot = resolve_pot(pot_id)
    return {
        "pot_id": pot_id,
        "total_brews": len(pot.brew_history),
        "brews": [r.to_dict() for r in pot.brew_history],
    }


# ── PROPFIND ──────────────────────────────────────────────────────────────────

@router.api_route("/coffee/{pot_id}/additions", methods=["PROPFIND"])
def propfind(pot_id: str):
    """
    PROPFIND — List all available additions for this pot.
    RFC 2324 §2.1.1 — Accept-Additions header values.
    """
    resolve_pot(pot_id)  # Validate pot exists
    log.info("htcpcp.propfind", pot_id=pot_id)
    return {
        **SUPPORTED_ADDITIONS,
        "decaf": DECAF_RESPONSE,
        "rfc": "RFC 2324 §2.1.1",
    }


# ── WHEN ──────────────────────────────────────────────────────────────────────

@router.api_route("/coffee/{pot_id}/stop-milk", methods=["WHEN"])
def when(pot_id: str):
    """
    WHEN — Stop pouring milk. The client determines when enough is enough.
    RFC 2324 §2.1.3

    This is the most human method in the history of network protocols.
    """
    pot = resolve_pot(pot_id)

    if pot.status != PotStatus.POURING_MILK:
        log.info("htcpcp.when_noop", pot_id=pot_id, current_status=pot.status)
        return JSONResponse(status_code=200, content={
            "message": "WHEN acknowledged.",
            "note": "No milk was being poured, but your enthusiasm is appreciated.",
            "current_status": pot.status,
            "rfc": "RFC 2324 §2.1.3",
        })

    pot.status = PotStatus.BREWING

    log.info("htcpcp.when_milk_stopped", pot_id=pot_id, status_code=200)

    return JSONResponse(status_code=200, content={
        "message": "Milk pouring stopped.",
        "detail": "The server has acknowledged WHEN and stopped the milk stream.",
        "current_status": pot.status,
        "protocol": "HTCPCP/1.0",
        "rfc": "RFC 2324 §2.1.3",
    })


# ── Registry ──────────────────────────────────────────────────────────────────

@router.get("/")
def registry():
    """List all registered pots."""
    from models import POT_REGISTRY
    return {
        "protocol": "HTCPCP/1.0",
        "rfc": ["RFC 2324", "RFC 7168"],
        "pots": {uri: pot.to_dict() for uri, pot in POT_REGISTRY.items()},
        "methods": ["BREW", "GET", "PROPFIND", "WHEN"],
        "supported_additions": list(SUPPORTED_ADDITIONS.keys()),
    }
