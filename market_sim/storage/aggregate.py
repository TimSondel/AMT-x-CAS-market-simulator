"""Agregacja strumienia transakcji: świece wolumenowe i volume profile."""

from __future__ import annotations

from dataclasses import dataclass, field

from market_sim.core.order import Trade


@dataclass
class Candle:
    """Świeca wolumenowa (nie czasowa)."""

    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    open_time: float = 0.0
    close_time: float = 0.0
    day_index: int = 0

    def add(self, price: float, size: int, timestamp: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += size
        self.close_time = timestamp


class CandleBuilder:
    """Buduje świece wolumenowe z overflow do następnej świecy."""

    def __init__(self, target_volume: int = 500, day_index: int = 0):
        if target_volume <= 0:
            raise ValueError("target_volume must be > 0")
        self.target_volume = int(target_volume)
        self.day_index = day_index
        self.candles: list[Candle] = []
        self._current: Candle | None = None

    @property
    def current(self) -> Candle | None:
        """Świeca w budowie (None, gdy poprzednia właśnie się domknęła)."""
        return self._current

    def add_trade(self, trade: Trade) -> int:
        """Dodaje transakcję; zwraca indeks ostatniej świecy, do której weszła."""
        remaining = trade.size
        last_index = len(self.candles) - 1
        while remaining > 0:
            if self._current is None:
                self._current = Candle(
                    open=trade.price,
                    high=trade.price,
                    low=trade.price,
                    close=trade.price,
                    open_time=trade.timestamp,
                    close_time=trade.timestamp,
                    day_index=self.day_index,
                )
            room = self.target_volume - self._current.volume
            take = min(remaining, room)
            self._current.add(trade.price, take, trade.timestamp)
            remaining -= take
            last_index = len(self.candles)
            if self._current.volume >= self.target_volume:
                self.candles.append(self._current)
                self._current = None
                last_index = len(self.candles) - 1
        return last_index

    def add_trades(self, trades: list[Trade]) -> list[Candle]:
        for trade in trades:
            self.add_trade(trade)
        return self.candles

    def finish(self) -> list[Candle]:
        """Domyka częściową świecę (jeśli są transakcje)."""
        if self._current is not None and self._current.volume > 0:
            self.candles.append(self._current)
            self._current = None
        return self.candles


@dataclass
class StepPoint:
    """Stan rynku po jednej turze symulacji (zapisywany w HDF5)."""

    turn: int
    time: float
    mid: float
    best_bid: float
    best_ask: float
    volume: int
    trades: int


@dataclass
class VolumeProfile:
    """Histogram wolumenu po cenie z POC/VAH/VAL."""

    day_index: int
    prices: list[float] = field(default_factory=list)
    volumes: list[int] = field(default_factory=list)
    poc: float | None = None
    vah: float | None = None
    val: float | None = None

    @property
    def total_volume(self) -> int:
        return sum(self.volumes)


def build_volume_profile(
    trades: list[Trade],
    tick_size: float,
    day_index: int = 0,
    value_area: float = 0.7,
) -> VolumeProfile:
    """Buduje profil po cenie (biny = tick_size) wraz z POC/VAH/VAL."""
    buckets: dict[float, int] = {}
    for trade in trades:
        price = round(round(trade.price / tick_size) * tick_size, 10)
        buckets[price] = buckets.get(price, 0) + trade.size

    prices = sorted(buckets)
    volumes = [buckets[price] for price in prices]
    profile = VolumeProfile(day_index=day_index, prices=prices, volumes=volumes)
    if not prices:
        return profile

    poc_index = max(range(len(volumes)), key=lambda i: volumes[i])
    profile.poc = prices[poc_index]

    target = profile.total_volume * value_area
    low = high = poc_index
    covered = volumes[poc_index]
    while covered < target and (low > 0 or high < len(prices) - 1):
        below = volumes[low - 1] if low > 0 else None
        above = volumes[high + 1] if high < len(prices) - 1 else None
        if above is not None and (below is None or above >= below):
            high += 1
            covered += above
        elif below is not None:
            low -= 1
            covered += below
        else:
            break
    profile.val = prices[low]
    profile.vah = prices[high]
    return profile
