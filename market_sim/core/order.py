"""Model danych zlecenia i transakcji (patrz ui.md 2.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from itertools import count


class Side(IntEnum):
    """Strona zlecenia."""

    BUY = 1
    SELL = -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


_order_ids = count(1)


def next_order_id() -> int:
    """Zwraca kolejny unikalny identyfikator zlecenia."""
    return next(_order_ids)


@dataclass
class Order:
    """Pojedyncze zlecenie w orderbooku."""

    side: Side
    price: float
    size: int
    timestamp: float = 0.0
    remaining: int = field(default=0)
    id: int = field(default_factory=next_order_id)
    tag: str | None = None
    agent_ref: object | None = None

    def __post_init__(self) -> None:
        self.side = Side(self.side)
        if self.remaining == 0:
            self.remaining = self.size

    @property
    def is_filled(self) -> bool:
        return self.remaining <= 0


@dataclass
class Trade:
    """Zrealizowana transakcja."""

    timestamp: float
    price: float
    size: int
    aggressor_side: Side
    day_index: int = 0
