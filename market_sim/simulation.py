"""Symulacja: krok tury, cykl dnia i EOD (logic.md 1.5-1.8)."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from market_sim.config import Config
from market_sim.core.order import Side, Trade
from market_sim.core.orderbook import OrderBook, Rejected
from market_sim.core.perception import (
    long_probability,
    sample_offset,
    sample_order_size,
)
from market_sim.core.processes import (
    CertaintyProcess,
    FairValueProcess,
    SpreadProcess,
    VisibilityProcess,
)
from market_sim.core.volume import intraday_multiplier, is_overnight


class PositionLedger:
    """Bilans otwartych pozycji po market orderach."""

    def __init__(self) -> None:
        self.positions: list[tuple[Side, int, float]] = []
        self.trade_count = 0
        self.closed_count = 0

    def register_trade(self, trade: Trade) -> None:
        self.positions.append((trade.aggressor_side, trade.size, trade.price))
        self.trade_count += 1

    def get_open(self) -> list[int]:
        return list(range(len(self.positions)))

    def mark_closed(self, index: int) -> None:
        """Zdejmuje pozycję z listy otwartych."""
        if index < 0 or index >= len(self.positions):
            raise ValueError("index out of range")
        self.closed_count += 1
        self.positions.pop(index)

    @property
    def open_count(self) -> int:
        return len(self.positions)

    @property
    def open_volume(self) -> int:
        return sum(size for _, size, _ in self.positions)


@dataclass
class StepRecord:
    """Stan rynku po jednej turze (do stopniowego rysowania świec)."""

    turn: int
    time: float
    mid: float
    best_bid: float
    best_ask: float
    volume: int
    trades: int


@dataclass
class DayResult:
    """Wynik jednego dnia symulacji."""

    day_index: int
    open_price: float | None
    close_price: float | None
    high: float | None
    low: float | None
    total_volume: int
    num_trades: int
    snapshots: list[tuple[float, float, float, float]] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    regime_path: list[int] = field(default_factory=list)
    accepted_limits: int = 0
    rejected_limits: int = 0
    market_orders: int = 0
    limit_orders: int = 0
    cancels: int = 0
    flash_crashes: int = 0
    silent_steps: int = 0
    open_positions_after: int = 0
    seeds: dict[str, int] = field(default_factory=dict)


class Simulation:
    """Silnik symulacji: procesy → percepcja → zlecenia → orderbook."""

    def __init__(self, config: Config, seed: int | None = None):
        self.config = config
        base_seed = config.seed if seed is None else seed
        self.seed = base_seed
        start_price = config.simulation.start_price
        self.rng_perception = random.Random(base_seed + 1)
        self.rng_flow = random.Random(base_seed + 2)
        self.rng_book = random.Random(base_seed + 3)
        self.rng_events = random.Random(base_seed + 4)

        self.orderbook = OrderBook(
            tick_size=config.tick_size,
            refill_decay_tau=config.orderbook.refill_decay_tau,
            partial_fill_mode=config.orderbook.partial_fill_mode,
            min_depth_ticks=config.orderbook.min_depth_ticks,
            mid_price=start_price,
            rng=self.rng_book,
        )
        self.orderbook.sample_order_size = (
            lambda: sample_order_size(config, self.rng_flow)
        )
        self.fair_value = FairValueProcess(
            config, self.rng_perception, start_value=start_price
        )
        self.spread = SpreadProcess(config, self.rng_perception)
        self.certainty = CertaintyProcess(config, self.rng_perception)
        self.visibility = VisibilityProcess(config, self.rng_perception)
        self.ledger = PositionLedger()
        self.turn = 0
        self.day_index = -1
        self.events: list[Trade] = []
        self.snapshots: list[tuple[float, float, float, float]] = []
        self.regime_path: list[int] = []
        self._day_trades: list[Trade] = []
        self._day_steps: list[StepRecord] = []
        self.stats = DayResult(
            day_index=-1,
            open_price=None,
            close_price=None,
            high=None,
            low=None,
            total_volume=0,
            num_trades=0,
        )

    # ------------------------------------------------------------------
    # 1.5 krok symulacji
    # ------------------------------------------------------------------
    def step(self, turn: int) -> float | None:
        """Wykonuje jedną turę; zwraca mid po kroku (None przy cichym kroku)."""
        config = self.config
        self.turn = turn
        time = turn * config.simulation.step_seconds

        # --- 2. aktualizacja procesów ---
        self.fair_value.update(config.simulation.step_seconds)
        spread_ticks = self.spread.update(
            config.simulation.step_seconds, config.tick_size
        )
        certainty = self.certainty.update(config.simulation.step_seconds)
        visibility = self.visibility.update(certainty, config.certainty.clip_max)
        self.regime_path.append(self.fair_value.regime.state)

        # --- 3. wolumen intraday ---
        volume_mult = intraday_multiplier(config, turn)
        if is_overnight(config, turn):
            if self.rng_events.random() > config.volume.overnight_activity:
                self.stats.silent_steps += 1
                return self._emit_snapshot(time)
        if volume_mult <= 0:
            self.stats.silent_steps += 1
            return self._emit_snapshot(time)

        mid = self.orderbook.mid_price()
        if mid is None:
            self.orderbook.refill()
            mid = self.orderbook.mid_price()
        if mid is None:
            return None

        # --- 4. percepcja → kierunek ---
        spread_price = max(spread_ticks * config.tick_size, config.tick_size)
        p_long = long_probability(
            self.fair_value.value, mid, spread_price, certainty, config.tick_size
        )
        direction = Side.BUY if self.rng_flow.random() < p_long else Side.SELL

        # --- 5. wybór typu zlecenia ---
        use_market = (
            self.rng_flow.random() < self.visibility.market_weight()
        )
        if use_market:
            self._submit_market(direction, volume_mult, certainty, time)
            self.orderbook.refill(min_ticks=config.orderbook.min_depth_ticks)
        else:
            self._submit_limit(
                direction, volume_mult, certainty, spread_ticks, mid, time
            )

        # --- 6. zdarzenia losowe ---
        if self.rng_events.random() < config.events.cancel_rate:
            if self.orderbook.cancel_random_order():
                self.stats.cancels += 1
        if self.rng_events.random() < config.events.flash_crash_rate:
            self.stats.flash_crashes += 1
            self.orderbook.execute_market(
                Side.SELL, config.events.flash_size, time
            )
            self.orderbook.refill(min_ticks=config.orderbook.min_depth_ticks)

        # --- 7. snapshot ---
        return self._emit_snapshot(time)

    def _submit_market(
        self, direction: Side, volume_mult: float, certainty: float, time: float
    ) -> None:
        raw = (
            sample_order_size(self.config, self.rng_flow)
            * volume_mult
            * certainty
        )
        size = max(1, int(round(raw)))
        market_before = len(self.orderbook.history)
        self.orderbook.execute_market(direction, size, time)
        self.stats.market_orders += 1
        for trade in self.orderbook.history[market_before:]:
            self._day_trades.append(trade)
            self.ledger.register_trade(trade)
        self._refresh_trade_stats()

    def _submit_limit(
        self,
        direction: Side,
        volume_mult: float,
        certainty: float,
        spread_ticks: float,
        mid: float,
        time: float,
    ) -> None:
        config = self.config
        limit_side = Side.BUY if direction is Side.BUY else Side.SELL
        offset = sample_offset(spread_ticks, certainty, self.rng_flow)
        sign = -1.0 if limit_side is Side.BUY else 1.0
        price = self.orderbook.round_to_tick(
            mid + sign * offset * config.tick_size
        )
        if price <= 0:
            price = config.tick_size

        size = sample_order_size(config, self.rng_flow) * volume_mult
        if limit_side is direction:
            size *= config.visibility.market_bias
        size = max(1, int(round(size)))

        try:
            self.orderbook.add_limit(
                limit_side, price, size, time, tag=f"day{self.day_index}"
            )
        except Rejected:
            self.stats.rejected_limits += 1
            return
        self.stats.limit_orders += 1
        self.stats.accepted_limits += 1
        self.orderbook.ensure_spread()

    def _emit_snapshot(self, time: float) -> float | None:
        mid = self.orderbook.mid_price()
        best_bid = self.orderbook.best_bid
        best_ask = self.orderbook.best_ask
        if mid is not None:
            self.snapshots.append((time, mid, best_bid or 0.0, best_ask or 0.0))
            self._day_steps.append(
                StepRecord(
                    turn=self.turn,
                    time=time,
                    mid=mid,
                    best_bid=best_bid or 0.0,
                    best_ask=best_ask or 0.0,
                    volume=self.stats.total_volume,
                    trades=self.stats.num_trades,
                )
            )
        return mid

    def _refresh_trade_stats(self) -> None:
        """Aktualizuje licznik transakcji i wolumenu na bieżąco."""
        trades = self._day_trades
        self.stats.num_trades = len(trades)
        self.stats.total_volume = sum(trade.size for trade in trades)
        if trades:
            prices = [trade.price for trade in trades]
            self.stats.high = max(prices)
            self.stats.low = min(prices)

    # ------------------------------------------------------------------
    # 1.6 cykl dnia
    # ------------------------------------------------------------------
    def run_day(self, day_index: int) -> DayResult:
        """Generuje jeden dzień symulacji i zwraca jego wynik."""
        config = self.config
        self.day_index = day_index
        self.snapshots = []
        self.regime_path = []
        self._day_trades = []
        self._day_steps = []
        self.stats = DayResult(
            day_index=day_index,
            open_price=None,
            close_price=None,
            high=None,
            low=None,
            total_volume=0,
            num_trades=0,
            seeds=self._seeds(),
        )

        # --- 1. inicjalizacja dnia ---
        self.fair_value.value = self.orderbook.mid_price() or config.simulation.start_price
        self.fair_value.ou.value = self.fair_value.value
        self.fair_value.set_regime(self.fair_value.regime.state)
        self.orderbook.refill(min_ticks=config.orderbook.min_depth_ticks)
        day_open_price = self.orderbook.mid_price()

        # --- 2. pętla tur ---
        for turn in range(1, config.turns_per_day + 1):
            self.step(turn)
            mid = self.orderbook.mid_price()
            if mid is not None and mid <= 0:
                break

        # --- 3. end of day cleanup ---
        day_close_price = self.orderbook.mid_price()
        self.close_open_positions(config.eod.close_pct)
        self.orderbook.thin_to(ticks=config.eod.keep_ticks)
        self.orderbook.refill(min_ticks=config.orderbook.min_depth_ticks)

        # --- 4. agregacja ---
        trades = list(self._day_trades)
        self.stats.trades = trades
        self.stats.snapshots = list(self.snapshots)
        self.stats.steps = list(self._day_steps)
        self.stats.regime_path = list(self.regime_path)
        self.stats.open_price = day_open_price
        self.stats.close_price = day_close_price
        self.stats.open_positions_after = self.ledger.open_count
        return self.stats

    # ------------------------------------------------------------------
    # 1.7 EOD
    # ------------------------------------------------------------------
    def close_open_positions(self, pct: float) -> int:
        """Zamyka `pct` otwartych pozycji po cenach rynkowych."""
        config = self.config
        open_positions = self.ledger.get_open()
        if not open_positions or pct <= 0:
            return 0
        count = int(round(len(open_positions) * pct))
        if count <= 0:
            return 0
        to_close = self.rng_events.sample(open_positions, min(count, len(open_positions)))

        mid = self.orderbook.mid_price()
        if mid is None:
            self.orderbook.refill()
            mid = self.orderbook.mid_price()
        time = self.turn * config.simulation.step_seconds
        if mid is not None:
            for side in (Side.BUY, Side.SELL):
                sign = -1.0 if side is Side.BUY else 1.0
                for i in range(1, config.eod.limit_levels + 1):
                    price = self.orderbook.round_to_tick(
                        mid + sign * i * config.tick_size
                    )
                    if price <= 0:
                        continue
                    try:
                        self.orderbook.add_limit(
                            side, price, config.eod.limit_size, time, tag="eod"
                        )
                    except Rejected:
                        continue

        closed = 0
        # od końca, żeby usuwanie pozycji nie przesuwało indeksów
        for index in sorted(to_close, reverse=True):
            side, size, _price = self.ledger.positions[index]
            close_side = Side.SELL if side is Side.BUY else Side.BUY
            self.orderbook.execute_market(close_side, size, time)
            self.ledger.mark_closed(index)
            closed += 1
        self.orderbook.remove_tagged("eod")
        self.orderbook.refill(min_ticks=config.eod.keep_ticks)
        return closed

    def _seeds(self) -> dict[str, int]:
        return {
            "seed": self.seed,
            "perception": self.seed + 1,
            "flow": self.seed + 2,
            "book": self.seed + 3,
            "events": self.seed + 4,
        }
