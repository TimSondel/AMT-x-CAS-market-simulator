"""Procesy stochastyczne: OU, jump-diffusion, reżimy Markowa, log-OU (functions.md 1-5)."""

from __future__ import annotations

import math
import random

from market_sim.config import Config


class RegimeProcess:
    """Przełączanie reżimów łańcuchem Markowa (quiet / normal / volatile)."""

    def __init__(self, matrix: list[list[float]], rng: random.Random, initial: int = 1):
        if not matrix or any(len(row) != len(matrix) for row in matrix):
            raise ValueError("macierz przejść musi być kwadratowa")
        self.matrix = [list(row) for row in matrix]
        self.rng = rng
        self.state = int(initial)

    @property
    def num_states(self) -> int:
        return len(self.matrix)

    def maybe_transition(self) -> int:
        """Losuje ewentualne przejście do innego reżimu."""
        row = self.matrix[self.state]
        u = self.rng.random()
        acc = 0.0
        for index, prob in enumerate(row):
            acc += prob
            if u < acc:
                self.state = index
                return self.state
        self.state = len(row) - 1
        return self.state


class OrnsteinUhlenbeck:
    """Proces OU: dX = theta * (mu - X) * dt + sigma * dW."""

    def __init__(self, theta: float, mu: float, sigma: float, rng: random.Random):
        self.theta = float(theta)
        self.mu = float(mu)
        self.sigma = float(sigma)
        self.rng = rng
        self.value = float(mu)

    def step(self, dt: float, sigma: float | None = None) -> float:
        s = self.sigma if sigma is None else sigma
        drift = self.theta * (self.mu - self.value) * dt
        shock = s * math.sqrt(dt) * self.rng.gauss(0.0, 1.0)
        self.value += drift + shock
        return self.value


class JumpDiffusion:
    """Skoki Poissona (Merton): J * dN."""

    def __init__(
        self,
        lam: float,
        jump_mu: float,
        jump_sigma: float,
        rng: random.Random,
    ):
        self.lam = float(lam)
        self.jump_mu = float(jump_mu)
        self.jump_sigma = float(jump_sigma)
        self.rng = rng

    def step(self, dt: float) -> float:
        if self.lam <= 0 or dt <= 0:
            return 0.0
        if self.rng.random() >= self.lam * dt:
            return 0.0
        return self.rng.gauss(self.jump_mu, self.jump_sigma)


class FairValueProcess:
    """Percepcja wartości: OU + jump-diffusion + regime switching."""

    def __init__(self, config: Config, rng: random.Random, start_value: float):
        perception = config.perception
        self.regime = RegimeProcess(perception.regime_transition_matrix, rng)
        self.sigmas = perception.regime_sigmas()
        self.ou = OrnsteinUhlenbeck(
            theta=perception.ou_theta,
            mu=start_value,
            sigma=self.sigmas[self.regime.state],
            rng=rng,
        )
        self.jumps = JumpDiffusion(
            lam=perception.jump_lambda,
            jump_mu=perception.jump_mu,
            jump_sigma=perception.jump_sigma,
            rng=rng,
        )
        self.value = float(start_value)
        self.jump_count = 0

    def update(self, dt: float) -> float:
        self.regime.maybe_transition()
        sigma = self.sigmas[self.regime.state]
        self.value = self.ou.step(dt, sigma=sigma)
        jump = self.jumps.step(dt)
        if jump:
            self.value += jump
            self.jump_count += 1
        return self.value

    def set_regime(self, state: int) -> None:
        """Ustawia reżim bez losowania (reset dnia / determinizm)."""
        self.regime.state = int(state) % self.regime.num_states


class LogOUProcess:
    """OU w przestrzeni logarytmicznej (zawsze dodatni, prawoskośny)."""

    def __init__(
        self,
        theta: float,
        mu: float,
        sigma: float,
        rng: random.Random,
        start: float | None = None,
    ):
        self.theta = float(theta)
        self.mu = float(mu)
        self.sigma = float(sigma)
        self.rng = rng
        self.log_value = math.log(start) if start else float(mu)

    def step(self, dt: float) -> float:
        drift = self.theta * (self.mu - self.log_value) * dt
        shock = self.sigma * math.sqrt(dt) * self.rng.gauss(0.0, 1.0)
        self.log_value += drift + shock
        return self.log_value

    @property
    def value(self) -> float:
        return math.exp(self.log_value)


class SpreadProcess:
    """Spread: log-OU przeliczony na ticki i przycięty do [min_ticks, max_ticks]."""

    def __init__(self, config: Config, rng: random.Random):
        spread = config.spread
        start_ticks = min(
            max(math.exp(spread.log_ou_mu), spread.min_ticks), spread.max_ticks
        )
        self.process = LogOUProcess(
            theta=spread.log_ou_theta,
            mu=spread.log_ou_mu,
            sigma=spread.log_ou_sigma,
            rng=rng,
            start=start_ticks,
        )
        self.min_ticks = spread.min_ticks
        self.max_ticks = spread.max_ticks
        self.ticks = start_ticks

    def update(self, dt: float, tick_size: float) -> float:
        self.process.step(dt)
        raw = self.process.value / tick_size
        self.ticks = min(max(raw, self.min_ticks), self.max_ticks)
        return self.ticks

    def offset_ticks(self, rng: random.Random) -> int:
        """Losowy offset limitu w tickach, ograniczony bieżącym spreadem."""
        low = 1
        high = max(low, int(round(self.ticks)))
        return rng.randint(low, high)


class CertaintyProcess:
    """Pewność: log-OU przycięty sigmoidalnie do [clip_min, clip_max]."""

    def __init__(self, config: Config, rng: random.Random):
        certainty = config.certainty
        start = min(max(math.exp(certainty.log_ou_mu), certainty.clip_min), certainty.clip_max)
        self.process = LogOUProcess(
            theta=certainty.log_ou_theta,
            mu=certainty.log_ou_mu,
            sigma=certainty.log_ou_sigma,
            rng=rng,
            start=start,
        )
        self.clip_min = certainty.clip_min
        self.clip_max = certainty.clip_max
        self.value = start

    def update(self, dt: float) -> float:
        self.process.step(dt)
        self.value = min(max(self.process.value, self.clip_min), self.clip_max)
        return self.value


class VisibilityProcess:
    """Widoczność: Beta(alpha, beta) w [0, 1], sprzężona z pewnością."""

    def __init__(self, config: Config, rng: random.Random):
        visibility = config.visibility
        self.alpha = visibility.beta_alpha
        self.beta = visibility.beta_beta
        self.market_bias = visibility.market_bias
        self.rng = rng
        self.value = self.alpha / (self.alpha + self.beta)

    def update(self, certainty: float, clip_max: float) -> float:
        logit_certainty = math.log(max(certainty, 1e-6))
        alpha = max(1e-3, self.alpha * math.exp(logit_certainty))
        self.value = self.rng.betavariate(alpha, self.beta)
        return self.value

    def market_weight(self) -> float:
        return min(1.0, max(0.0, self.value * self.market_bias))
