"""Odczyt zapisanej symulacji z HDF5."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np


class HDF5Reader:
    """Czyta meta, indeks dni i dane pojedynczego dnia."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def meta(self) -> dict:
        with h5py.File(self.path, "r") as handle:
            meta = handle["meta"]
            data = dict(meta.attrs)
            if "config_yaml" in meta:
                data["config_yaml"] = meta["config_yaml"][()]
            return data

    def days(self) -> list[str]:
        with h5py.File(self.path, "r") as handle:
            if "days" not in handle:
                return []
            return sorted(handle["days"].keys())

    def index(self) -> np.ndarray:
        with h5py.File(self.path, "r") as handle:
            if "index" not in handle:
                return np.empty((0, 3), dtype="f8")
            return np.asarray(handle["index"][()], dtype="f8")

    def day(self, day_index: int) -> dict:
        with h5py.File(self.path, "r") as handle:
            group = handle["days"][f"day_{day_index:04d}"]
            payload = {
                "trades": np.asarray(group["trades"][()]),
                "candles": np.asarray(group["candles"][()]),
                "prices": np.asarray(group["profile"]["prices"][()]),
                "volumes": np.asarray(group["profile"]["volumes"][()]),
                "meta": dict(group["meta"].attrs),
            }
            if "steps" in group:
                payload["steps"] = np.asarray(group["steps"][()])
            return payload
