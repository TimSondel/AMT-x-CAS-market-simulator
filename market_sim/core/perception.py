"""Percepcja, kierunek i rozkłady zleceń (functions.md 2, 5, 7)."""

from __future__ import annotations

import math
import random

from market_sim.config import Config


def sigmoid(x: float) -> float:
    """Stabilna numerycznie sigmoida."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


def normal_cdf(x: float) -> float:
    """Dystrybuanta rozkładu normalnego (wersja CDF-owa z functions.md 2)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def long_probability(
    center: float, mid: float, spread: float, certainty: float, tick: float
) -> float:
    """p_long = sigmoid(certainty * z), z = (center - mid) / max(spread, tick)."""
    z = (center - mid) / max(spread, tick)
    return sigmoid(certainty * z)


def long_probability_cdf(
    center: float, mid: float, spread: float, tick: float
) -> float:
    """Wariant CDF-owy: 0.5 * (1 + erf(z / sqrt(2)))."""
    z = (center - mid) / max(spread, tick)
    return normal_cdf(z)


def sample_order_size(config: Config, rng: random.Random) -> int:
    """Rozmiar zlecenia: log-normalny z ogonem Pareto (block trades)."""
    flow = config.order_flow
    size = rng.lognormvariate(flow.size_mu, flow.size_sigma)
    if rng.random() < flow.block_prob:
        size *= rng.paretovariate(flow.block_pareto_alpha) + 1.0
    return max(1, min(flow.max_size, int(round(size))))


def sample_offset(spread_ticks: float, certainty: float, rng: random.Random) -> int:
    """Offset limitu od mid w tickach: co najmniej 1, zwykle w granicach spreadu."""
    span = max(1, int(round(spread_ticks)))
    return rng.randint(1, span)
