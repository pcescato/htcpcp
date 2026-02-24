"""
HTCPCP/1.0 — Data Models
RFC 2324 (coffee) + RFC 7168 (tea)
"""

from enum import Enum
from dataclasses import dataclass, field
from time import time


class PotType(str, Enum):
    COFFEE = "coffee"
    TEAPOT = "teapot"


class PotStatus(str, Enum):
    IDLE = "idle"
    BREWING = "brewing"
    POURING_MILK = "pouring-milk"
    READY = "ready"


@dataclass
class BrewRecord:
    id: int
    timestamp: float
    additions: dict[str, str]
    status: str = "completed"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "additions": self.additions,
            "status": self.status,
        }


@dataclass
class CoffeePot:
    id: str
    pot_type: PotType
    capacity: int
    level: int
    varieties: list[str] = field(default_factory=list)
    status: PotStatus = field(default=PotStatus.IDLE)
    brew_history: list[BrewRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pot_id": self.id,
            "type": self.pot_type,
            "status": self.status,
            "level": self.level,
            "capacity": self.capacity,
            "level_display": f"{self.level}/{self.capacity} cups",
            "varieties": self.varieties,
            "brew_count": len(self.brew_history),
        }

    def add_brew(self, additions: dict) -> BrewRecord:
        record = BrewRecord(
            id=len(self.brew_history) + 1,
            timestamp=time(),
            additions=additions,
        )
        self.brew_history.append(record)
        return record


# ── RFC 2324 §2.1.1 — Supported additions ────────────────────────────────────

SUPPORTED_ADDITIONS: dict[str, list[str]] = {
    "milk-type": ["Cream", "Half-and-half", "Whole-milk", "Part-Skim", "Skim", "Non-Dairy"],
    "syrup-type": ["Vanilla", "Almond", "Raspberry", "Chocolate"],
    "sweetener-type": ["Sugar", "Honey", "Artificial"],
    "spice-type":     ["Cinnamon", "Cardamom"],
    "alcohol-type":   ["Whisky", "Rum", "Kahlua", "Aquavit"],
}

# RFC 2324 §2.1.1 — no decaf, intentionally.
# "What's the point?" — Larry Masinter, 1998
DECAF_RESPONSE = "NOT_ACCEPTABLE — What's the point? (RFC 2324 §2.1.1)"


# ── Pot registry ──────────────────────────────────────────────────────────────

POT_REGISTRY: dict[str, CoffeePot] = {
    "coffee://pot-1": CoffeePot(
        id="pot-1",
        pot_type=PotType.COFFEE,
        capacity=12,
        level=8,
        varieties=["Espresso", "Lungo", "Americano"],
    ),
    "coffee://pot-2": CoffeePot(
        id="pot-2",
        pot_type=PotType.COFFEE,
        capacity=6,
        level=2,
        varieties=["Espresso"],
    ),
    "tea://kettle-1": CoffeePot(
        id="kettle-1",
        pot_type=PotType.TEAPOT,
        capacity=8,
        level=6,
        varieties=["Earl Grey", "Chamomile", "Darjeeling"],
    ),
    "tea://kettle-2": CoffeePot(
        id="kettle-2",
        pot_type=PotType.TEAPOT,
        capacity=4,
        level=4,
        varieties=["Oolong"],
    ),
}


def get_pot(pot_id: str) -> CoffeePot | None:
    """Lookup a pot by ID, checking both coffee:// and tea:// URIs."""
    return (
        POT_REGISTRY.get(f"coffee://{pot_id}")
        or POT_REGISTRY.get(f"tea://{pot_id}")
    )
