"""Ładowanie i walidacja konfiguracji (YAML + pydantic)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


class SimulationConfig(BaseModel):
    tick_size: float = Field(gt=0)
    step_seconds: float = Field(gt=0)
    turns_per_day: int = Field(gt=0)
    days_to_generate: int = Field(gt=0)
    start_price: float = Field(default=100.0, gt=0)
    day_start: int = 0
    day_end: int = 0


class OrderBookConfig(BaseModel):
    min_depth_ticks: int = Field(gt=0)
    refill_decay_tau: float = Field(gt=0)
    partial_fill_mode: str = "synthetic"

    @field_validator("partial_fill_mode")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        allowed = ("cancel", "synthetic", "panic")
        if value not in allowed:
            raise ValueError(f"partial_fill_mode must be one of {allowed}")
        return value


class PerceptionConfig(BaseModel):
    ou_theta: float = Field(gt=0)
    ou_sigma_quiet: float = Field(ge=0)
    ou_sigma_normal: float = Field(ge=0)
    ou_sigma_volatile: float = Field(ge=0)
    jump_lambda: float = Field(ge=0)
    jump_mu: float
    jump_sigma: float = Field(ge=0)
    regime_transition_matrix: list[list[float]]

    @field_validator("regime_transition_matrix")
    @classmethod
    def _square(cls, value: list[list[float]]) -> list[list[float]]:
        if not value or any(len(row) != len(value) for row in value):
            raise ValueError("regime_transition_matrix must be square")
        for row in value:
            if abs(sum(row) - 1.0) > 1e-6:
                raise ValueError("each regime row must sum to 1")
        return value

    @property
    def num_regimes(self) -> int:
        return len(self.regime_transition_matrix)

    def regime_sigmas(self) -> list[float]:
        return [
            self.ou_sigma_quiet,
            self.ou_sigma_normal,
            self.ou_sigma_volatile,
        ][: self.num_regimes]


class LogOUConfig(BaseModel):
    log_ou_mu: float
    log_ou_theta: float = Field(gt=0)
    log_ou_sigma: float = Field(ge=0)


class SpreadConfig(LogOUConfig):
    min_ticks: int = Field(gt=0)
    max_ticks: int = Field(gt=0)


class CertaintyConfig(LogOUConfig):
    clip_min: float = Field(gt=0)
    clip_max: float = Field(gt=0)


class VisibilityConfig(BaseModel):
    beta_alpha: float = Field(gt=0)
    beta_beta: float = Field(gt=0)
    market_bias: float = Field(gt=0)


class VolumeConfig(BaseModel):
    open_peak_amp: float = Field(ge=0)
    open_peak_sigma: float = Field(gt=0)
    close_peak_amp: float = Field(ge=0)
    close_peak_sigma: float = Field(gt=0)
    midday_baseline: float = Field(ge=0)
    overnight_factor: float = Field(ge=0)
    overnight_activity: float = Field(ge=0, le=1)
    candle_target_volume: int = Field(default=500, gt=0)


class EodConfig(BaseModel):
    close_pct: float = Field(ge=0, le=1)
    limit_levels: int = Field(ge=0)
    limit_size: int = Field(gt=0)
    keep_ticks: int = Field(gt=0)


class OrderFlowConfig(BaseModel):
    size_mu: float = 0.0
    size_sigma: float = 1.0
    block_prob: float = Field(default=0.05, ge=0, le=1)
    block_pareto_alpha: float = Field(default=1.5, gt=0)
    max_size: int = Field(default=10_000, gt=0)


class EventsConfig(BaseModel):
    cancel_rate: float = Field(default=0.02, ge=0, le=1)
    flash_crash_rate: float = Field(default=0.0, ge=0, le=1)
    flash_size: int = Field(default=1000, gt=0)


class Config(BaseModel):
    simulation: SimulationConfig
    orderbook: OrderBookConfig
    perception: PerceptionConfig
    spread: SpreadConfig
    certainty: CertaintyConfig
    visibility: VisibilityConfig
    volume: VolumeConfig
    eod: EodConfig
    order_flow: OrderFlowConfig = Field(default_factory=OrderFlowConfig)
    events: EventsConfig = Field(default_factory=EventsConfig)
    seed: int = 0

    @property
    def tick_size(self) -> float:
        return self.simulation.tick_size

    @property
    def turns_per_day(self) -> int:
        return self.simulation.turns_per_day


def load_config(path: str | Path | None = None) -> Config:
    """Wczytuje config.yaml i waliduje go modelem pydantic."""
    config_path = Path(path) if path is not None else CONFIG_PATH
    with open(config_path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return Config.model_validate(raw)
