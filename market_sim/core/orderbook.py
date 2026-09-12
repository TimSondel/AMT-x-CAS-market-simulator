"""OrderBook — dodawanie limitów, wykonanie market, refill.

Implementacja zgodna z logic.md 1.1-1.3.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Iterable, List, Tuple

from sortedcontainers import SortedDict

from market_sim.core.order import Order, Side, Trade


class Rejected(Exception):
    """Zlecenie odrzucone przez walidację (logic.md 1.1)."""


@dataclass
class Level:
    """Poziom cenowy: cena, łączny rozmiar i kolejka zleceń FIFO."""

    price: float
    total_size: int = 0
    orders: List[Order] = field(default_factory=list)


PARTIAL_FILL_MODES = ("cancel", "synthetic", "panic")


class OrderBook:
    """Orderbook z dopasowaniem FIFO i refillem głębokości."""

    def __init__(
        self,
        tick_size: float = 0.25,
        *,
        refill_decay_tau: float = 5.0,
        partial_fill_mode: str = "synthetic",
        min_depth_ticks: int = 20,
        mid_price: float | None = None,
        rng: random.Random | None = None,
    ) -> None:
        if tick_size <= 0:
            raise ValueError("tick_size must be > 0")
        if partial_fill_mode not in PARTIAL_FILL_MODES:
            raise ValueError(
                f"partial_fill_mode must be one of {PARTIAL_FILL_MODES}"
            )
        self.tick_size = float(tick_size)
        self.refill_decay_tau = float(refill_decay_tau)
        self.partial_fill_mode = partial_fill_mode
        self.min_depth_ticks = int(min_depth_ticks)
        self.mid_price_ref = mid_price
        self.rng = rng if rng is not None else random.Random()

        self.bids: SortedDict = SortedDict()
        self.asks: SortedDict = SortedDict()
        self.history: List[Trade] = []
        self._refilling = False
        self._panicking = False

    # ------------------------------------------------------------------
    # widoki stanu
    # ------------------------------------------------------------------
    @property
    def best_bid(self) -> float | None:
        """Najwyższa cena kupna lub None."""
        if not self.bids:
            return None
        return self.bids.peekitem(len(self.bids) - 1)[0]

    @property
    def best_ask(self) -> float | None:
        """Najniższa cena sprzedaży lub None."""
        if not self.asks:
            return None
        return self.asks.peekitem(0)[0]

    def mid_price(self) -> float | None:
        """Środek spreadu; przy jednostronnym booku best ± tick."""
        bid, ask = self.best_bid, self.best_ask
        if bid is not None and ask is not None:
            return (bid + ask) / 2.0
        if bid is not None:
            return bid + self.tick_size
        if ask is not None:
            return ask - self.tick_size
        return self.mid_price_ref

    def levels(self, side: Side) -> SortedDict:
        """SortedDict poziomów dla podanej strony."""
        return self.bids if Side(side) is Side.BUY else self.asks

    def count_levels(
        self,
        side: Side,
        from_price: float,
        direction: float,
        within: int,
    ) -> int:
        """Liczba istniejących poziomów w promieniu `within` ticków (z anchor)."""
        book = self.levels(side)
        found = 0
        for i in range(within + 1):
            if self._key(book, from_price + i * direction) is not None:
                found += 1
        return found

    def ensure_spread(self) -> None:
        """Naprawia puste lub skrzyżowane best (log.md 1.5 krok 5c)."""
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None or bid < ask:
            return
        keep = self.levels(Side.BUY).pop(bid)
        new_price = self.round_to_tick(ask - self.tick_size)
        if new_price <= 0 or new_price >= ask:
            self.levels(Side.BUY)[bid] = keep
            return
        keep.price = new_price
        self.levels(Side.BUY)[new_price] = keep

    # ------------------------------------------------------------------
    # 1.1 dodanie zlecenia LIMIT
    # ------------------------------------------------------------------
    def add_limit(
        self,
        side: Side,
        price: float,
        size: int,
        timestamp: float = 0.0,
        *,
        tag: str | None = None,
        order_id: int | None = None,
    ) -> Tuple[int, int]:
        """Dodaje zlecenie LIMIT.

        Zwraca (filled, remaining). Zlecenie odrzucone → Rejected.
        """
        side = Side(side)
        if size <= 0:
            raise Rejected("size <= 0")
        price = float(price)
        if not self._is_on_tick(price):
            price = self.round_to_tick(price)
        if price <= 0:
            raise Rejected("price <= 0")
        if order_id is not None and self._has_order(order_id):
            raise Rejected(f"duplicate order id {order_id}")

        if side is Side.BUY:
            best_ask = self.best_ask
            if best_ask is not None and price >= best_ask:
                filled, remaining = self.execute_market(Side.BUY, size, timestamp)
                if remaining > 0 and not self._refilling:
                    self._rest(
                        Side.BUY,
                        remaining,
                        timestamp,
                        tag,
                        order_id,
                        fallback_price=price,
                    )
                return filled, remaining
        else:
            best_bid = self.best_bid
            if best_bid is not None and price <= best_bid:
                filled, remaining = self.execute_market(
                    Side.SELL, size, timestamp
                )
                if remaining > 0 and not self._refilling:
                    self._rest(
                        Side.SELL,
                        remaining,
                        timestamp,
                        tag,
                        order_id,
                        fallback_price=price,
                    )
                return filled, remaining

        self._append(side, price, size, timestamp, tag, order_id)
        self._assert_not_crossed()
        return 0, size

    # ------------------------------------------------------------------
    # 1.2 wykonanie zlecenia MARKET
    # ------------------------------------------------------------------
    def execute_market(
        self, side: Side, size: int, timestamp: float = 0.0
    ) -> Tuple[int, int]:
        """Wykonuje zlecenie MARKET. Zwraca (filled_total, remaining)."""
        side = Side(side)
        if size <= 0:
            return 0, 0

        filled_total = 0
        opposite = self.levels(side.opposite)
        while size > 0 and opposite:
            best_index = 0 if side is Side.BUY else len(opposite) - 1
            price, level = opposite.peekitem(best_index)
            take = min(size, level.total_size)
            remaining_take = take
            while remaining_take > 0 and level.orders:
                order = level.orders[0]
                consume = min(remaining_take, order.remaining)
                order.remaining -= consume
                remaining_take -= consume
                self._emit_trade(side, price, consume, timestamp)
                if order.remaining == 0:
                    level.orders.pop(0)
            level.total_size -= take
            size -= take
            filled_total += take
            if level.total_size <= 0:
                del opposite[price]

        if size > 0:
            if self.partial_fill_mode == "cancel":
                pass
            elif self.partial_fill_mode == "synthetic":
                self._queue_for_next_step(side, size, timestamp)
            elif self.partial_fill_mode == "panic":
                if not self._panicking:
                    self._panicking = True
                    try:
                        self.refill()
                        extra_filled, size = self.execute_market(
                            side, size, timestamp
                        )
                        filled_total += extra_filled
                    finally:
                        self._panicking = False
        self._assert_not_crossed()
        return filled_total, size

    # ------------------------------------------------------------------
    # 1.3 REFILL po zjedzeniu
    # ------------------------------------------------------------------
    def refill(self, min_ticks: int | None = None, timestamp: float = 0.0) -> None:
        """Uzupełnia brakujące poziomy po obu stronach booka."""
        if min_ticks is None:
            min_ticks = self.min_depth_ticks
        min_ticks = int(min_ticks)
        self._refilling = True
        try:
            self._ensure_initial_spread()
            for side in (Side.BUY, Side.SELL):
                anchor = self._anchor(side)
                step = -self.tick_size if side is Side.BUY else self.tick_size
                self._fill_band(side, anchor, step, min_ticks, timestamp)
        finally:
            self._refilling = False
        self._assert_not_crossed()

    def _fill_band(
        self,
        side: Side,
        anchor: float,
        step: float,
        min_ticks: int,
        timestamp: float,
    ) -> None:
        """Dopełnia pasmo `min_ticks` poziomów, rozciągnięte od kotwicy na zewnątrz."""
        book = self.levels(side)
        candidates = self._band_candidates(side, anchor, step, min_ticks)
        missing = sum(
            1 for price in candidates if self._key(book, price) is None
        )
        if missing <= 0:
            return
        for k, price in enumerate(candidates):
            if missing <= 0:
                break
            if price <= 0 or self._key(book, price) is not None:
                continue
            size = max(
                1,
                round(
                    self.sample_order_size()
                    * math.exp(-k / self.refill_decay_tau)
                ),
            )
            self._append(side, price, size, timestamp, "refill", None)
            missing -= 1

    def _band_candidates(
        self, side: Side, anchor: float, step: float, min_ticks: int
    ) -> List[float]:
        """Kandydaci na poziomy: od kotwicy na zewnątrz booka."""
        return [
            self.round_to_tick(anchor + k * step) for k in range(min_ticks)
        ]

    def _fits_in_book(self, side: Side, price: float) -> bool:
        """Czy cena nie przechodzi na drugą stronę booka."""
        if side is Side.BUY:
            best_ask = self.best_ask
            return best_ask is None or price < best_ask
        best_bid = self.best_bid
        return best_bid is None or price > best_bid

    def _ensure_initial_spread(self) -> None:
        """Start na pustym booku: wstrzykuje kwotowanie wokół ceny odniesienia."""
        if self.bids or self.asks or self.mid_price_ref is None:
            return
        mid = self.round_to_tick(self.mid_price_ref)
        bid = self.round_to_tick(mid - self.tick_size)
        ask = self.round_to_tick(mid + self.tick_size)
        if bid > 0:
            self._append(
                Side.BUY, bid, self.sample_order_size(), 0.0, "refill", None
            )
        if ask > 0:
            self._append(
                Side.SELL, ask, self.sample_order_size(), 0.0, "refill", None
            )

    def sample_order_size(self) -> int:
        """Bazowy rozmiar zlecenia refillu (nadpisywalny w testach)."""
        return max(1, round(self.rng.lognormvariate(0.0, 1.0)))

    # ------------------------------------------------------------------
    # pomocnicze
    # ------------------------------------------------------------------
    def round_to_tick(self, price: float, tick: float | None = None) -> float:
        """Zaokrągla cenę do wielokrotności ticku."""
        step = self.tick_size if tick is None else tick
        return round(round(price / step) * step, 10)

    def _is_on_tick(self, price: float) -> bool:
        return math.isclose(price / self.tick_size, round(price / self.tick_size))

    @staticmethod
    def _key(book: SortedDict, price: float):
        """Klucz poziomu odporny na błąd reprezentacji float."""
        if price in book:
            return price
        for key in book:
            if math.isclose(key, price, rel_tol=0.0, abs_tol=1e-9):
                return key
        return None

    def _has_order(self, order_id: int) -> bool:
        for book in (self.bids, self.asks):
            for level in book.values():
                if any(order.id == order_id for order in level.orders):
                    return True
        return False

    def _anchor(self, side: Side) -> float:
        """Punkt startowy rozszerzania booka (logic.md 1.3)."""
        if side is Side.BUY:
            best = self.best_bid
            if best is not None:
                return best
            best_ask = self.best_ask
            if best_ask is not None:
                return best_ask - self.tick_size
        else:
            best = self.best_ask
            if best is not None:
                return best
            best_bid = self.best_bid
            if best_bid is not None:
                return best_bid + self.tick_size
        mid = self.mid_price()
        if mid is None:
            raise ValueError("cannot anchor refill without mid price")
        return mid - self.tick_size if side is Side.BUY else mid + self.tick_size

    def _append(
        self,
        side: Side,
        price: float,
        size: int,
        timestamp: float,
        tag: str | None,
        order_id: int | None,
    ) -> Order:
        book = self.levels(side)
        price = self.round_to_tick(price)
        level = book.get(price)
        if level is None:
            level = Level(price=price)
            book[price] = level
        if order_id is None:
            order = Order(
                side=side, price=price, size=size, timestamp=timestamp, tag=tag
            )
        else:
            order = Order(
                side=side,
                price=price,
                size=size,
                timestamp=timestamp,
                tag=tag,
                id=order_id,
            )
        level.orders.append(order)
        level.total_size += size
        return order

    def _rest(
        self,
        side: Side,
        size: int,
        timestamp: float,
        tag: str | None,
        order_id: int | None,
        fallback_price: float | None = None,
    ) -> None:
        """Reszta agresywnego limitu ląduje po swojej stronie (logic.md 1.1)."""
        price: float | None
        if side is Side.BUY:
            best_bid = self.best_bid
            price = (
                self.round_to_tick(best_bid + self.tick_size)
                if best_bid is not None
                else self._safe_anchor(Side.BUY)
            )
        else:
            best_ask = self.best_ask
            price = (
                self.round_to_tick(best_ask - self.tick_size)
                if best_ask is not None
                else self._safe_anchor(Side.SELL)
            )
        if price is None or price <= 0:
            price = fallback_price
        if price is not None and price > 0:
            self._append(side, price, size, timestamp, tag, order_id)

    def _safe_anchor(self, side: Side) -> float | None:
        try:
            return self.round_to_tick(self._anchor(side))
        except ValueError:
            return None

    def _queue_for_next_step(
        self, side: Side, size: int, timestamp: float
    ) -> None:
        """Tryb synthetic: resztę dopisujemy do booka na następny krok."""
        self._rest(
            side,
            size,
            timestamp,
            "synthetic",
            None,
            fallback_price=self._safe_anchor(side),
        )

    def _emit_trade(
        self, aggressor_side: Side, price: float, size: int, timestamp: float
    ) -> None:
        self.history.append(
            Trade(
                timestamp=timestamp,
                price=price,
                size=size,
                aggressor_side=Side(aggressor_side),
            )
        )

    def _assert_not_crossed(self) -> None:
        bid, ask = self.best_bid, self.best_ask
        if bid is not None and ask is not None and bid >= ask:
            raise AssertionError(f"crossed book: best_bid={bid} best_ask={ask}")

    # ------------------------------------------------------------------
    # operacje porządkowe (cancel / thin / remove_tagged)
    # ------------------------------------------------------------------
    def cancel_random_order(self) -> bool:
        """Anuluje losowe zlecenie z booka; True jeśli coś usunięto."""
        volumes = len(self.bids) + len(self.asks)
        if volumes == 0:
            return False
        for _ in range(4):
            side = self.rng.choice((Side.BUY, Side.SELL))
            book = self.levels(side)
            if not book:
                continue
            index = self.rng.randrange(len(book))
            price, level = book.peekitem(index)
            order = self.rng.choice(level.orders)
            level.orders.remove(order)
            level.total_size -= order.remaining
            if level.total_size <= 0 or not level.orders:
                del book[price]
            return True
        return False

    def remove_tagged(self, tag: str) -> int:
        """Usuwa wszystkie zlecenia z podanym tagiem; zwraca liczbę usuniętych."""
        removed = 0
        for book in (self.bids, self.asks):
            for price in list(book):
                level = book[price]
                keep = [order for order in level.orders if order.tag != tag]
                removed += len(level.orders) - len(keep)
                if not keep:
                    del book[price]
                    continue
                level.orders = keep
                level.total_size = sum(order.remaining for order in keep)
        return removed

    def thin_to(self, ticks: int) -> None:
        """Ścina book do `ticks` poziomów na stronę, od najdalszych."""
        ticks = max(0, int(ticks))
        for book in (self.bids, self.asks):
            while len(book) > ticks:
                book.popitem(len(book) - 1)

    def __iter__(self) -> Iterable[Level]:
        for book in (self.bids, self.asks):
            yield from book.values()
