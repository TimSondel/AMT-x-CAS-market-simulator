"""Warstwa danych dla UI: wczytanie zapisu HDF5 do struktur dla widoku."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from market_sim.core.order import Side, Trade
from market_sim.storage.reader import HDF5Reader
from market_sim.ui.timeline import Timeline, build_timeline


@dataclass
class DayMeta:
    """Metryki jednego dnia z zapisu."""

    day_index: int
    open: float | None
    close: float | None
    high: float | None
    low: float | None
    total_volume: int
    num_trades: int
    candle_count: int


class SimulationData:
    """Zawartość pliku symulacji gotowa dla widoku."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.reader = HDF5Reader(self.path)
        self.meta = self.reader.meta()
        self.index = self.reader.index()
        self._metas: dict[int, DayMeta] = {}
        self._trades: dict[int, list[Trade]] = {}
        self._timeline: Timeline | None = None

    @property
    def seed(self) -> int:
        return int(self.meta.get("seed", 0))

    @property
    def version(self) -> str:
        return str(self.meta.get("version", "?"))

    def day_indices(self) -> list[int]:
        """Numery dni obecne w pliku (z indeksu lub z grup)."""
        if self.index.size:
            return [int(row[0]) for row in self.index]
        return [int(name.split("_")[-1]) for name in self.reader.days()]

    def day_count(self) -> int:
        return len(self.day_indices())

    def load_trades(self, day_index: int) -> list[Trade]:
        """Transakcje dnia (z cache)."""
        if day_index not in self._trades:
            payload = self.reader.day(day_index)
            self._trades[day_index] = [
                Trade(
                    timestamp=float(row["timestamp"]),
                    price=float(row["price"]),
                    size=int(row["size"]),
                    aggressor_side=Side(int(row["aggressor_side"])),
                    day_index=int(row["day_index"]),
                )
                for row in payload["trades"]
            ]
            self._metas[day_index] = self._meta_from(day_index, payload)
        return self._trades[day_index]

    def day_meta(self, day_index: int) -> DayMeta:
        if day_index not in self._metas:
            self.load_trades(day_index)
        return self._metas[day_index]

    def timeline(self, target_volume: int, refresh: bool = False) -> Timeline:
        """Wspólna oś czasu świec wolumenowych dla wszystkich dni."""
        if self._timeline is None or refresh:
            days = [
                (day_index, self.load_trades(day_index))
                for day_index in self.day_indices()
            ]
            self._timeline = build_timeline(days, target_volume)
        return self._timeline

    def _meta_from(self, day_index: int, payload: dict) -> DayMeta:
        meta = payload["meta"]
        return DayMeta(
            day_index=day_index,
            open=_optional(meta.get("open")),
            close=_optional(meta.get("close")),
            high=_optional(meta.get("high")),
            low=_optional(meta.get("low")),
            total_volume=int(meta.get("total_volume", 0)),
            num_trades=int(meta.get("num_trades", len(payload["trades"]))),
            candle_count=int(len(payload["candles"])),
        )


def _optional(value) -> float | None:
    if value is None:
        return None
    value = float(value)
    return None if value != value else value  # NaN -> None
