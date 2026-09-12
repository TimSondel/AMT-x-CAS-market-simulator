"""Wolumen intraday — kształt U (functions.md 6)."""

from __future__ import annotations

import math

from market_sim.config import Config


def volume_curve(
    t: float,
    open_peak_amp: float = 3.0,
    open_peak_sigma: float = 0.05,
    close_peak_amp: float = 2.5,
    close_peak_sigma: float = 0.05,
    midday_baseline: float = 0.5,
) -> float:
    """Mieszanina dwóch Gaussów + baseline; t w [0, 1]."""
    t = min(1.0, max(0.0, t))
    open_peak = open_peak_amp * math.exp(
        -((t - 0.03) ** 2) / (2.0 * open_peak_sigma**2)
    )
    close_peak = close_peak_amp * math.exp(
        -((t - 0.97) ** 2) / (2.0 * close_peak_sigma**2)
    )
    return open_peak + close_peak + midday_baseline


def volume_curve_from_config(config: Config, t: float) -> float:
    volume = config.volume
    return volume_curve(
        t,
        open_peak_amp=volume.open_peak_amp,
        open_peak_sigma=volume.open_peak_sigma,
        close_peak_amp=volume.close_peak_amp,
        close_peak_sigma=volume.close_peak_sigma,
        midday_baseline=volume.midday_baseline,
    )


def intraday_multiplier(config: Config, turn: int) -> float:
    """Mnożnik wolumenu dla tury; 0 oznacza brak aktywności (overnight)."""
    total = config.turns_per_day
    t_norm = turn / total if total else 0.0
    multiplier = volume_curve_from_config(config, t_norm)
    if is_overnight(config, turn):
        multiplier *= config.volume.overnight_factor
    return multiplier


def is_overnight(config: Config, turn: int) -> bool:
    """Noc, jeśli okno dnia nie pokrywa całego zakresu tur."""
    simulation = config.simulation
    if simulation.day_end <= simulation.day_start:
        return False
    return not (simulation.day_start <= turn < simulation.day_end)
