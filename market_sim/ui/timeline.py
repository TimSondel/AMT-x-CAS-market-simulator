"""Oś czasu świec wolumenowych dla UI.

Świece budowane są wyłącznie z transakcji: każda świeca zbiera dokładnie
`target_volume` wolumenu (nadmiar transakcji przechodzi do kolejnej świecy).
Oś czasu to numer świecy; postęp odtwarzania to indeks transakcji, więc
świeca rośnie w trakcie każdej transakcji, a domyka się przy pełnym wolumenie.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from market_sim.core.order import Trade
from market_sim.storage.aggregate import Candle, CandleBuilder


@dataclass
class DaySpan:
    """Zakres jednego dnia na wspólnej osi świec."""

    day_index: int
    start_candle: int
    end_candle: int
    first_trade: int
    last_trade: int
    open: float
    close: float
    total_volume: int
    candle_target_volume: int


@dataclass
class Timeline:
    """Wszystkie dni na jednej osi świec wolumenowych."""

    candles: list[Candle] = field(default_factory=list)
    spans: list[DaySpan] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    # dla każdej transakcji: indeks świecy, do której weszła
    trade_candle: list[int] = field(default_factory=list)
    closed_after: list[bool] = field(default_factory=list)
    # narastający wolumen w obrębie świecy po każdej transakcji
    volume_at: list[int] = field(default_factory=list)

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def candle_count(self) -> int:
        return len(self.candles)

    def day_start_trade(self, day_index: int) -> int:
        """Indeks transakcji, od której zaczyna się dzień."""
        for span in self.spans:
            if span.day_index == day_index:
                return span.first_trade
        return 0

    def span_for_candle(self, candle_index: int) -> DaySpan | None:
        for span in self.spans:
            if span.start_candle <= candle_index <= span.end_candle:
                return span
        return None

    def day_boundaries(self) -> list[tuple[int, int]]:
        """(indeks świecy, numer dnia) dla początku każdego dnia poza pierwszym."""
        return [
            (span.start_candle, span.day_index)
            for span in self.spans
            if span.start_candle > 0
        ]

    def state_at(self, trade_position: int) -> tuple[int, bool]:
        """Zwraca (indeks bieżącej świecy, czy świeca jest już domknięta).

        Pozycja 0 oznacza stan przed pierwszą transakcją: świeca jeszcze nie
        istnieje (indeks 0, ale nic nie jest rysowane).
        """
        if self.candle_count == 0:
            return -1, True
        if trade_position <= 0:
            return 0, False
        position = min(trade_position, self.trade_count)
        index = self.trade_candle[position - 1]
        return index, bool(self.closed_after[position - 1])

    def volume_in_candle(self, trade_position: int) -> int:
        """Wolumen zebrany w bieżącej świecy po `trade_position` transakcjach."""
        if trade_position <= 0 or not self.volume_at:
            return 0
        return self.volume_at[min(trade_position, self.trade_count) - 1]

    def partial_candle(
        self, candle_index: int, trade_position: int
    ) -> Candle | None:
        """Świeca w budowie: tylko transakcje do `trade_position` włącznie."""
        if not 0 <= candle_index < self.candle_count or trade_position <= 0:
            return None
        end = min(trade_position, self.trade_count)
        # ostatnia transakcja, która domknęła poprzednią świecę
        previous_closed = end - 1
        while previous_closed > 0 and not self.closed_after[previous_closed - 1]:
            previous_closed -= 1
        start = previous_closed + 1
        if start > end:
            return None
        volume = self.volume_at[end - 1]
        prices = [
            self.trades[i].price
            for i in range(start - 1, end)
            if self.trade_candle[i] == candle_index
        ]
        if not prices:
            return None
        return Candle(
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=max(0, volume),
            open_time=self.trades[start - 1].timestamp,
            close_time=self.trades[end - 1].timestamp,
            day_index=self.candles[candle_index].day_index,
        )


def build_timeline(
    days: list[tuple[int, list[Trade]]],
    target_volume: int,
) -> Timeline:
    """Buduje wspólną oś czasu z transakcji wszystkich dni."""
    if target_volume < 1:
        raise ValueError("target_volume must be >= 1")
    timeline = Timeline()
    for day_index, day_trades in days:
        first_trade = len(timeline.trades)
        start_candle = len(timeline.candles)
        builder = CandleBuilder(target_volume=target_volume, day_index=day_index)
        running_volume = 0
        for trade in day_trades:
            local_index = builder.add_trade(trade)
            last_candle = start_candle + local_index
            done = (
                0 <= local_index < len(builder.candles)
                and builder.candles[local_index].volume >= target_volume
            )
            running_volume = 0 if done else running_volume + trade.size
            timeline.trades.append(trade)
            timeline.trade_candle.append(last_candle)
            timeline.closed_after.append(done)
            timeline.volume_at.append(running_volume)
        builder.finish()
        timeline.candles.extend(builder.candles)
        for candle in timeline.candles[start_candle:]:
            candle.day_index = day_index
        timeline.spans.append(
            DaySpan(
                day_index=day_index,
                start_candle=start_candle,
                end_candle=max(start_candle, len(timeline.candles) - 1),
                first_trade=first_trade,
                last_trade=max(first_trade, len(timeline.trades) - 1),
                open=day_trades[0].price if day_trades else 0.0,
                close=day_trades[-1].price if day_trades else 0.0,
                total_volume=sum(trade.size for trade in day_trades),
                candle_target_volume=target_volume,
            )
        )
    return timeline
