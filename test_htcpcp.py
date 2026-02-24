"""
HTCPCP/1.0 — Test Suite
Because even absurd protocols deserve rigorous tests.
"""

import pytest
from fastapi.testclient import TestClient

from main import app
from models import POT_REGISTRY, PotStatus

client = TestClient(app, raise_server_exceptions=False)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_registry():
    """Reset pot states between tests."""
    for pot in POT_REGISTRY.values():
        pot.status = PotStatus.IDLE
        pot.brew_history = []
        pot.level = pot.capacity  # refill between tests
    yield


# ── Registry ──────────────────────────────────────────────────────────────────

def test_registry_lists_all_pots():
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["protocol"] == "HTCPCP/1.0"
    assert "coffee://pot-1" in data["pots"]
    assert "tea://kettle-1" in data["pots"]


def test_protocol_headers_present():
    r = client.get("/")
    assert r.headers["x-protocol"] == "HTCPCP/1.0"
    assert "RFC-2324" in r.headers["x-rfc"]


# ── BREW ──────────────────────────────────────────────────────────────────────

def test_brew_coffee_pot_returns_200():
    r = client.request("BREW", "/coffee/pot-1")
    assert r.status_code == 200
    data = r.json()
    assert data["message"] == "Coffee is brewing."
    assert data["brew_id"] == 1
    assert data["protocol"] == "HTCPCP/1.0"


def test_brew_with_additions():
    r = client.request(
        "BREW", "/coffee/pot-1",
        headers={"Accept-Additions": "milk-type=Whole-milk; alcohol-type=Whisky"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["accept-additions"]["milk-type"] == "Whole-milk"
    assert data["accept-additions"]["alcohol-type"] == "Whisky"
    assert data["milk_pouring"] is True
    assert data["when_required"] is True


def test_brew_records_history():
    client.request("BREW", "/coffee/pot-1")
    client.request("BREW", "/coffee/pot-1")
    r = client.get("/coffee/pot-1/history")
    assert r.json()["total_brews"] == 2


# ── 418 ───────────────────────────────────────────────────────────────────────

def test_teapot_cannot_brew_returns_418():
    """RFC 2324 §2.3.2 — The most important test in this file."""
    r = client.request("BREW", "/coffee/kettle-1")
    assert r.status_code == 418
    data = r.json()
    assert data["error"] == "I'm a teapot"
    assert data["body"] == "The requested entity body is short and stout."
    assert data["hint"] == "Tip me over and pour me out."
    assert data["rfc"] == "RFC 2324 §2.3.2"


def test_brew_on_wrong_route_returns_418():
    """Middleware: BREW on a non-coffee route → 418."""
    r = client.request("BREW", "/users/1")
    assert r.status_code == 418
    assert r.json()["error"] == "Wrong universe"


# ── 406 ───────────────────────────────────────────────────────────────────────

def test_decaf_is_not_acceptable():
    """RFC 2324 §2.1.1 — Decaf explicitly rejected. What's the point?"""
    r = client.request(
        "BREW", "/coffee/pot-1",
        headers={"Accept-Additions": "decaf=true"},
    )
    assert r.status_code == 406
    data = r.json()
    assert "What's the point?" in data["detail"]["message"]
    assert data["detail"]["rfc"] == "RFC 2324 §2.1.1"


def test_invalid_milk_type_returns_406():
    r = client.request(
        "BREW", "/coffee/pot-1",
        headers={"Accept-Additions": "milk-type=Oat-milk"},  # Not in RFC
    )
    assert r.status_code == 406


# ── GET ───────────────────────────────────────────────────────────────────────

def test_get_status_returns_pot_info():
    r = client.get("/coffee/pot-1/status")
    assert r.status_code == 200
    data = r.json()
    assert data["pot_id"] == "pot-1"
    assert data["type"] == "coffee"
    assert "level_display" in data


def test_get_unknown_pot_returns_404():
    r = client.get("/coffee/nonexistent/status")
    assert r.status_code == 404


# ── PROPFIND ──────────────────────────────────────────────────────────────────

def test_propfind_lists_additions():
    r = client.request("PROPFIND", "/coffee/pot-1/additions")
    assert r.status_code == 200
    data = r.json()
    assert "milk-type" in data
    assert "Whole-milk" in data["milk-type"]
    assert "Whisky" in data["alcohol-type"]
    assert "NOT_ACCEPTABLE" in data["decaf"]


# ── WHEN ──────────────────────────────────────────────────────────────────────

def test_when_stops_milk_pouring():
    """RFC 2324 §2.1.3 — The most human method in network history."""
    # First brew with milk to enter POURING_MILK state
    client.request(
        "BREW", "/coffee/pot-1",
        headers={"Accept-Additions": "milk-type=Whole-milk"},
    )
    # Now say WHEN
    r = client.request("WHEN", "/coffee/pot-1/stop-milk")
    assert r.status_code == 200
    data = r.json()
    assert "stopped" in data["message"]
    assert data["rfc"] == "RFC 2324 §2.1.3"


def test_when_with_no_milk_pouring_is_graceful():
    r = client.request("WHEN", "/coffee/pot-1/stop-milk")
    assert r.status_code == 200
    assert "enthusiasm" in r.json()["note"]


# ── 503 ───────────────────────────────────────────────────────────────────────

def test_empty_pot_returns_503_not_418():
    """
    A coffee pot that's empty should return 503, NOT 418.
    Common mistake. The pot is a coffee pot — it's just empty.
    """
    POT_REGISTRY["coffee://pot-1"].level = 0
    r = client.request("BREW", "/coffee/pot-1")
    assert r.status_code == 503
    assert "empty" in r.json()["detail"]["message"].lower()
