"""Zapis symulacji do HDF5 (ui.md 2.4)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from market_sim.config import Config
from market_sim.simulation import DayResult
from market_sim.storage.aggregate import Candle, VolumeProfile

FORMAT_VERSION = "1.0"

TRADE_DTYPE = np.dtype(
    [
        ("timestamp", "f8"),
        ("price", "f8"),
        ("size", "i8"),
        ("aggressor_side", "i1"),
        ("day_index", "i4"),
    ]
)

CANDLE_DTYPE = np.dtype(
    [
        ("open", "f8"),
        ("high", "f8"),
        ("low", "f8"),
        ("close", "f8"),
        ("volume", "i8"),
        ("open_time", "f8"),
        ("close_time", "f8"),
        ("day_index", "i4"),
    ]
)

STEP_DTYPE = np.dtype(
    [
        ("turn", "i4"),
        ("time", "f8"),
        ("mid", "f8"),
        ("best_bid", "f8"),
        ("best_ask", "f8"),
        ("volume", "i8"),
        ("trades", "i4"),
    ]
)


def trades_to_array(trades: list) -> np.ndarray:
    array = np.empty(len(trades), dtype=TRADE_DTYPE)
    for i, trade in enumerate(trades):
        array[i] = (
            trade.timestamp,
            trade.price,
            trade.size,
            int(trade.aggressor_side),
            trade.day_index,
        )
    return array


def candles_to_array(candles: list[Candle]) -> np.ndarray:
    array = np.empty(len(candles), dtype=CANDLE_DTYPE)
    for i, candle in enumerate(candles):
        array[i] = (
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
            candle.open_time,
            candle.close_time,
            candle.day_index,
        )
    return array


def steps_to_array(steps: list) -> np.ndarray:
    array = np.empty(len(steps), dtype=STEP_DTYPE)
    for i, step in enumerate(steps):
        array[i] = (
            step.turn,
            step.time,
            step.mid,
            step.best_bid,
            step.best_ask,
            step.volume,
            step.trades,
        )
    return array


class HDF5Writer:
    """Zapisuje meta, dni (trades/candles/profile) i indeks do pliku HDF5."""

    def __init__(self, path: str | Path, config: Config, seed: int):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.seed = seed
        self._index: list[tuple[int, float, float]] = []

    def write_day(
        self,
        day: DayResult,
        candles: list[Candle],
        profile: VolumeProfile,
    ) -> None:
        with h5py.File(self.path, "a") as handle:
            self._write_meta(handle)
            group = handle.require_group("days").require_group(
                f"day_{day.day_index:04d}"
            )
            if "trades" in group:
                del group["trades"]
            if "candles" in group:
                del group["candles"]
            if "steps" in group:
                del group["steps"]
            if "profile" in group:
                del group["profile"]
            group.create_dataset("trades", data=trades_to_array(day.trades))
            group.create_dataset("candles", data=candles_to_array(candles))
            group.create_dataset("steps", data=steps_to_array(day.steps))
            profile_group = group.create_group("profile")
            profile_group.create_dataset(
                "prices", data=np.asarray(profile.prices, dtype="f8")
            )
            profile_group.create_dataset(
                "volumes", data=np.asarray(profile.volumes, dtype="i8")
            )
            profile_group.attrs["poc"] = profile.poc if profile.poc is not None else np.nan
            profile_group.attrs["vah"] = profile.vah if profile.vah is not None else np.nan
            profile_group.attrs["val"] = profile.val if profile.val is not None else np.nan
            meta = group.create_group("meta") if "meta" not in group else group["meta"]
            meta.attrs["open"] = _nan(day.open_price)
            meta.attrs["close"] = _nan(day.close_price)
            meta.attrs["high"] = _nan(day.high)
            meta.attrs["low"] = _nan(day.low)
            meta.attrs["total_volume"] = day.total_volume
            meta.attrs["num_trades"] = day.num_trades
            meta.attrs["open_positions_after"] = day.open_positions_after
            meta.attrs["market_orders"] = day.market_orders
            meta.attrs["limit_orders"] = day.limit_orders
            meta.attrs["rejected_limits"] = day.rejected_limits
            meta.attrs["cancels"] = day.cancels
            meta.attrs["flash_crashes"] = day.flash_crashes
            meta.attrs["silent_steps"] = day.silent_steps
            meta.attrs["regime_path"] = np.asarray(day.regime_path, dtype="i4")

            self._index = [
                entry for entry in self._index if entry[0] != day.day_index
            ]
            self._index.append((day.day_index, day.open_price or 0.0, day.close_price or 0.0))
            if "index" in handle:
                del handle["index"]
            handle.create_dataset(
                "index", data=np.asarray(sorted(self._index), dtype="f8")
            )

    def _write_meta(self, handle: h5py.File) -> None:
        meta = handle.require_group("meta")
        meta.attrs["seed"] = self.seed
        meta.attrs["version"] = FORMAT_VERSION
        meta.attrs["created_at"] = datetime.now(timezone.utc).isoformat()
        if "config_yaml" in meta:
            del meta["config_yaml"]
        meta.create_dataset(
            "config_yaml", data=self.config.model_dump_json(indent=2)
        )


def _nan(value: float | None) -> float:
    return float("nan") if value is None else float(value)
