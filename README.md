# HTCPCP/1.0 — Coffee Pot Control Server

> Hyper Text Coffee Pot Control Protocol · RFC 2324 + RFC 7168

A production-grade implementation of the most important protocol you've never deployed.

## Quickstart

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 2324
```

The server runs on port **2324** — the RFC number. Obviously.

## Endpoints

| Method | URI | Description |
|--------|-----|-------------|
| `BREW` | `/coffee/{pot_id}` | Trigger an infusion |
| `GET` | `/coffee/{pot_id}/status` | Current pot state |
| `GET` | `/coffee/{pot_id}/history` | Brew history |
| `PROPFIND` | `/coffee/{pot_id}/additions` | List valid additions |
| `WHEN` | `/coffee/{pot_id}/stop-milk` | Stop pouring milk |
| `GET` | `/` | Full pot registry |

Interactive docs: http://localhost:2324/htcpcp-docs

## Example requests

```bash
# Brew a coffee with milk and whisky (Irish coffee — RFC compliant)
curl -X BREW http://localhost:2324/coffee/pot-1 \
  -H "Accept-Additions: milk-type=Whole-milk; alcohol-type=Whisky"

# Check pot status
curl http://localhost:2324/coffee/pot-1/status

# List available additions
curl -X PROPFIND http://localhost:2324/coffee/pot-1/additions

# Stop the milk
curl -X WHEN http://localhost:2324/coffee/pot-1/stop-milk

# Try brewing with a teapot (spoiler: 418)
curl -X BREW http://localhost:2324/coffee/kettle-1

# Try ordering decaf (spoiler: 406)
curl -X BREW http://localhost:2324/coffee/pot-1 \
  -H "Accept-Additions: decaf=true"
```

## Registered pots

| URI | Type | Varieties |
|-----|------|-----------|
| `coffee://pot-1` | ☕ Coffee pot | Espresso, Lungo, Americano |
| `coffee://pot-2` | ☕ Coffee pot | Espresso |
| `tea://kettle-1` | 🫖 Teapot | Earl Grey, Chamomile, Darjeeling |
| `tea://kettle-2` | 🫖 Teapot | Oolong |

## HTTP Status codes

| Code | Meaning |
|------|---------|
| `200` | Coffee is brewing |
| `406` | Not Acceptable (decaf attempted, or invalid addition) |
| `418` | I'm a teapot — BREW sent to a teapot |
| `503` | Pot is empty — refill required |

> ⚠️ An empty coffee pot returns **503**, not 418. The pot is still a coffee pot — it's just empty. Common mistake.

## Tests

```bash
pytest test_htcpcp.py -v
```

## RFC references

- [RFC 2324](https://tools.ietf.org/html/rfc2324) — Hyper Text Coffee Pot Control Protocol (1 April 1998)
- [RFC 7168](https://tools.ietf.org/html/rfc7168) — HTCPCP-TEA: Tea Efflux Appliances (1 April 2014)
