# -*- coding: utf-8 -*-
"""
Symulator rynku – Stały bufor płynności (OrderBook 100 poziomów) + Prosta adaptacja FV.

Implementuje wprost blueprint "CAS + AMT": rynek jest czysto emergentny – cena
powstaje wyłącznie z interakcji zleceń Market (składanych przez agentów)
z pasywną płynnością (OrderBook). Jedynym nośnikiem adaptacji jest zmiana
subiektywnej wartości godziwej (FV) agentów PO zamknięciu transakcji.

Uruchomienie (biblioteka standardowa + opcjonalny matplotlib do wykresów):

    python market_simulator.py                 # 2000 tur, 300 agentów, seed=42
    python market_simulator.py --turns 5000 --plot
    python market_simulator.py --turns 3000 --csv historia.csv --plot-save wykres.png
    python market_simulator.py --seed 7 --agents 500 --price 100 --tick 0.05

Mapa do blueprintu (sekcje):
  2  Agent            -> klasa Agent
  3  OrderBook        -> klasa OrderBook (bufor 100 poziomów + replenishment)
  4  Adaptacja FV     -> Agent.adapt_after_close()
  5  Wymuszenie akcji -> Simulator._step_forced_action()
  6  Drzewo tury      -> Simulator.step()
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple


# --------------------------------------------------------------------------- #
# Konfiguracja
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    """Parametry symulacji (mapa do blueprintu)."""

    seed: int = 42
    start_price: float = 100.0
    tick: float = 0.05              # krok siatki cenowej bufora
    n_levels: int = 100             # liczba poziomów po każdej stronie bufora
    n_agents: int = 300
    n_turns: int = 2000

    # Inicjalizacja FV (sekcja 2): FV ~ U[start*(1-fv_spread), start*(1+fv_spread)]
    fv_spread: float = 0.05

    # Próg wejścia (sekcja 2): |dist| > entry_threshold
    entry_threshold: float = 0.005

    # SL / TP jako procent od ceny wejścia (sekcja 2)
    sl_pct: float = 0.008
    tp_pct: float = 0.012
    sl_noise: float = 0.01         # +/- szum doliczany do SL (heterogeniczność)
    tp_noise: float = 0.02         # +/- szum doliczany do TP

    # Wielkość pozycji – mieszanka 3 rozkładów normalnych (mali/średni/duzi)
    vol_mix_weights: Tuple[float, float, float] = (0.6, 0.3, 0.1)
    vol_mix_mu: Tuple[float, float, float] = (5.0, 50.0, 400.0)
    vol_mix_sigma: Tuple[float, float, float] = (1.5, 12.0, 120.0)

    # Pasywna płynność bufora (sekcja 3): wykładniczy spadek głębokości od ceny
    base_passive: float = 1000.0    # wolumen na poziomie najbliższym ceny
    passive_decay: float = 0.05     # exp(-decay * (indeks_poziomu - 1))

    # Adaptacja FV (sekcja 4): FV += U(adapt_min, adapt_max) * (target - FV)
    adapt_min: float = 0.10
    adapt_max: float = 0.90

    forced_volume: float = 1.0      # minimalny wolumen wymuszonej akcji (sekcja 5)


# --------------------------------------------------------------------------- #
# Typy zleceń / transakcji
# --------------------------------------------------------------------------- #
@dataclass
class MarketOrder:
    """Zlecenie Market składane przez agenta (wejście, SL lub wymuszenie)."""

    side: str          # 'buy' | 'sell'
    volume: float
    agent_id: int
    kind: str          # 'entry' | 'sl' | 'forced' | 'forced_close'


@dataclass
class LimitOrder:
    """Zlecenie Limit oczekujące w książce (tu: wyłącznie TP agenta)."""

    order_id: int
    agent_id: int
    side: str          # 'sell' (TP longa) | 'buy' (TP shorta)
    price: float
    remaining: float


@dataclass
class Trade:
    """Pojedyncza transakcja z dopasowania."""

    price: float
    volume: float
    aggressor_side: str
    aggressor_agent: int


@dataclass
class Agent:
    """Jeden ujednolicony typ agenta (sekcja 2 blueprintu)."""

    agent_id: int
    fv: float
    volume: float                 # typowa wielkość pozycji (loty)

    # pozycja: >0 = long, <0 = short, 0 = flat
    position: float = 0.0
    entry_price: Optional[float] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None

    def __post_init__(self) -> None:
        self.realized_pnl: float = 0.0
        self.n_trades: int = 0

    @property
    def is_flat(self) -> bool:
        return abs(self.position) < 1e-12

    @property
    def side(self) -> str:
        return "long" if self.position > 0 else ("short" if self.position < 0 else "flat")

    def dist(self, price: float) -> float:
        """Względne odchylenie ceny od FV: (cena - FV) / FV."""
        return (price - self.fv) / self.fv

    def enter(self, price: float, side: str, sl_pct: float, tp_pct: float) -> None:
        """Otwarcie pozycji market po średniej cenie wykonania."""
        self.entry_price = price
        if side == "buy":
            self.position = self.volume
            self.sl_price = price * (1.0 - sl_pct)
            self.tp_price = price * (1.0 + tp_pct)
        else:
            self.position = -self.volume
            self.sl_price = price * (1.0 + sl_pct)
            self.tp_price = price * (1.0 - tp_pct)

    def close(self, close_price: float) -> float:
        """Zamknięcie pozycji, zwraca zrealizowany PnL."""
        pnl = self.position * (close_price - self.entry_price) if self.entry_price else 0.0
        self.realized_pnl += pnl
        self.n_trades += 1
        self.position = 0.0
        self.entry_price = self.sl_price = self.tp_price = None
        return pnl

    def adapt_after_close(self, target_price: float, rng: random.Random, cfg: Config) -> None:
        """Sekcja 4: przesuń FV w stronę ceny TP/SL o losowy ułamek 10%-90%."""
        frac = rng.uniform(cfg.adapt_min, cfg.adapt_max)
        self.fv = max(1e-9, self.fv + frac * (target_price - self.fv))


# --------------------------------------------------------------------------- #
# OrderBook – centralny mechanizm płynności (sekcja 3)
# --------------------------------------------------------------------------- #
class OrderBook:
    """
    Bufor 100 poziomów po każdej stronie ceny. Płynność pasywna jest
    utrzymywana algorytmicznie (replenishment); zlecenia Limit (TP agentów)
    czekają w książce jako dodatkowa warstwa na swoich poziomach.
    """

    def __init__(self, cfg: Config, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.passive_asks: Dict[float, float] = {}   # cena -> pasywny wolumen (sprzedaż)
        self.passive_bids: Dict[float, float] = {}   # cena -> pasywny wolumen (kupno)
        self.sell_limits: Dict[float, deque] = {}    # cena -> kolejka LimitOrder (TP longów)
        self.buy_limits: Dict[float, deque] = {}     # cena -> kolejka LimitOrder (TP shortów)
        self._next_order_id: int = 0
        self._touched_asks: Set[float] = set()
        self._touched_bids: Set[float] = set()
        self._level_index_cache: Dict[Tuple[float, float], int] = {}

    # -- narzędzia ---------------------------------------------------------- #
    def _price_at(self, anchor: float, k: int) -> float:
        return round(anchor + k * self.cfg.tick, 6)

    def _level_index(self, price: float, anchor: float) -> int:
        return int(round((price - anchor) / self.cfg.tick))

    def _passive_volume(self, anchor: float, k: int) -> float:
        """Wykładniczo malejąca głębokość: przy cenie dużo, daleko mało."""
        mean = self.cfg.base_passive * math.exp(-self.cfg.passive_decay * (k - 1))
        return max(1.0, self.rng.uniform(0.5, 1.5) * mean)

    def _refill_level(self, book: Dict[float, float], price: float, anchor: float) -> None:
        book[price] = self._passive_volume(anchor, self._level_index(price, anchor))

    # -- inicjalizacja / replenishment -------------------------------------- #
    def initialize(self, start_price: float) -> None:
        """Sekcja 3 (Start): 100 poziomów Ask powyżej i 100 Bid poniżej ceny."""
        for k in range(1, self.cfg.n_levels + 1):
            self.passive_asks[self._price_at(start_price, k)] = self._passive_volume(start_price, k)
            self.passive_bids[self._price_at(start_price, -k)] = self._passive_volume(start_price, k)

    def replenish(self, anchor: float) -> None:
        """
        Sekcja 3 (Replenishment) + drzewo tury krok 1:
          * uzupełnij poziomy częściowo/całkowicie wykupione nowym losowym wolumenem,
          * przesuń okno, aby zawsze było 100 poziomów po każdej stronie ceny,
          * usuń stare, odległe poziomy (bez limitów TP).
        """
        # 1) Uzupełnienie dotkniętych poziomów nową losową płynnością.
        for p in list(self._touched_asks):
            if p in self.passive_asks:
                self._refill_level(self.passive_asks, p, anchor)
        for p in list(self._touched_bids):
            if p in self.passive_bids:
                self._refill_level(self.passive_bids, p, anchor)
        self._touched_asks.clear()
        self._touched_bids.clear()

        # 2) Okno 100 poziomów względem bieżącej ceny (dokładanie krawędzi).
        target_asks = {self._price_at(anchor, k) for k in range(1, self.cfg.n_levels + 1)}
        target_bids = {self._price_at(anchor, -k) for k in range(1, self.cfg.n_levels + 1)}

        for p in target_asks:
            if p not in self.passive_asks:
                self._refill_level(self.passive_asks, p, anchor)
        for p in target_bids:
            if p not in self.passive_bids:
                self._refill_level(self.passive_bids, p, anchor)

        # 3) Usunięcie poziomów poza oknem (tylko bez limitów TP).
        for p in list(self.passive_asks):
            if p not in target_asks and p not in self.sell_limits:
                del self.passive_asks[p]
        for p in list(self.passive_bids):
            if p not in target_bids and p not in self.buy_limits:
                del self.passive_bids[p]

    # -- zlecenia limit ----------------------------------------------------- #
    def add_limit(self, agent_id: int, side: str, price: float, volume: float) -> None:
        book = self.sell_limits if side == "sell" else self.buy_limits
        self._next_order_id += 1
        book.setdefault(price, deque()).append(
            LimitOrder(self._next_order_id, agent_id, side, price, volume)
        )

    def cancel_limits(self, agent_id: int) -> None:
        """Usuń wszystkie oczekujące limity (TP) danego agenta."""
        for book in (self.sell_limits, self.buy_limits):
            for price in list(book):
                dq = book[price]
                book[price] = deque(o for o in dq if o.agent_id != agent_id)
                if not book[price]:
                    del book[price]

    # -- dopasowanie -------------------------------------------------------- #
    def match(self, mo: MarketOrder) -> Tuple[List[Trade], List[Tuple[int, float]]]:
        """
        Dopasuj zlecenie Market do limitów. Zwraca listę transakcji oraz listę
        (agent_id, cena) wypełnionych limitów TP (zamknięcie pozycji z zyskiem).
        """
        trades: List[Trade] = []
        filled: List[Tuple[int, float]] = []
        remaining = mo.volume

        if mo.side == "buy":
            passive = self.passive_asks
            limits = self.sell_limits
            touched = self._touched_asks
            prices = sorted(set(passive) | set(limits))
        else:
            passive = self.passive_bids
            limits = self.buy_limits
            touched = self._touched_bids
            prices = sorted(set(passive) | set(limits), reverse=True)

        for p in prices:
            if remaining <= 1e-12:
                break
            # 1) najpierw pasywna płynność poziomu
            pv = passive.get(p, 0.0)
            if pv > 0.0:
                take = min(remaining, pv)
                passive[p] = pv - take
                touched.add(p)
                remaining -= take
                trades.append(Trade(p, take, mo.side, mo.agent_id))
            # 2) potem oczekujące limity TP (FIFO)
            dq = limits.get(p)
            while dq and remaining > 1e-12:
                lo = dq[0]
                take = min(remaining, lo.remaining)
                lo.remaining -= take
                remaining -= take
                trades.append(Trade(p, take, mo.side, mo.agent_id))
                if lo.remaining <= 1e-12:
                    dq.popleft()
                    filled.append((lo.agent_id, p))
                else:
                    break
            if not dq:
                limits.pop(p, None)

        return trades, filled

    # -- ceny informacyjne -------------------------------------------------- #
    def best_ask(self) -> Optional[float]:
        prices = sorted(set(self.passive_asks) | set(self.sell_limits))
        return prices[0] if prices else None

    def best_bid(self) -> Optional[float]:
        prices = sorted(set(self.passive_bids) | set(self.buy_limits), reverse=True)
        return prices[0] if prices else None


# --------------------------------------------------------------------------- #
# Symulator
# --------------------------------------------------------------------------- #
class Simulator:
    """Orkiestruje pojedynczą turę wg drzewa decyzyjnego (sekcja 6)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.agents: List[Agent] = []
        self.book = OrderBook(cfg, self.rng)
        self.last_price = cfg.start_price
        self.history: List[dict] = []

        self._init_agents()
        self.book.initialize(cfg.start_price)

    # -- inicjalizacja ------------------------------------------------------ #
    def _sample_volume(self) -> float:
        """Mieszanka 3 rozkładów normalnych: mali / średni / duzi gracze."""
        w = self.cfg.vol_mix_weights
        r = self.rng.random()
        if r < w[0]:
            v = self.rng.gauss(self.cfg.vol_mix_mu[0], self.cfg.vol_mix_sigma[0])
        elif r < w[0] + w[1]:
            v = self.rng.gauss(self.cfg.vol_mix_mu[1], self.cfg.vol_mix_sigma[1])
        else:
            v = self.rng.gauss(self.cfg.vol_mix_mu[2], self.cfg.vol_mix_sigma[2])
        return max(1.0, float(round(v)))

    def _init_agents(self) -> None:
        lo = self.cfg.start_price * (1 - self.cfg.fv_spread)
        hi = self.cfg.start_price * (1 + self.cfg.fv_spread)
        for i in range(self.cfg.n_agents):
            fv = self.rng.uniform(lo, hi)
            vol = self._sample_volume()
            self.agents.append(Agent(i, fv, vol))

    # -- statystyki --------------------------------------------------------- #
    def _vwap(self, trades: Sequence[Trade]) -> Optional[float]:
        total = sum(t.volume for t in trades)
        return sum(t.price * t.volume for t in trades) / total if total > 0 else None

    # -- kroki tury --------------------------------------------------------- #
    def _step_sl_tp(self) -> List[MarketOrder]:
        """Krok 2: sprawdź przebicie SL (market). TP czeka jako limit w książce."""
        orders: List[MarketOrder] = []
        for a in self.agents:
            if a.is_flat:
                continue
            if a.position > 0 and self.last_price <= a.sl_price:
                orders.append(MarketOrder("sell", a.position, a.agent_id, "sl"))
            elif a.position < 0 and self.last_price >= a.sl_price:
                orders.append(MarketOrder("buy", -a.position, a.agent_id, "sl"))
        return orders

    def _step_entry_decisions(self) -> List[MarketOrder]:
        """Krok 3: kontrariańskie wejścia dla |dist| > progu."""
        orders: List[MarketOrder] = []
        thr = self.cfg.entry_threshold
        for a in self.agents:
            if not a.is_flat:
                continue
            d = a.dist(self.last_price)
            if d < -thr:
                orders.append(MarketOrder("buy", a.volume, a.agent_id, "entry"))
            elif d > thr:
                orders.append(MarketOrder("sell", a.volume, a.agent_id, "entry"))
        return orders

    def _step_forced_action(self) -> List[MarketOrder]:
        """Krok 4: wymuszenie akcji, aby żadna tura nie była martwa."""
        flat = [a for a in self.agents if a.is_flat]
        if flat:
            # agent o najmniejszym |dist| (wciąż poniżej progu) – wymuś wejście.
            best = min(flat, key=lambda a: abs(a.dist(self.last_price)))
            if best.fv > self.last_price:
                side = "buy"
            elif best.fv < self.last_price:
                side = "sell"
            else:
                side = self.rng.choice(["buy", "sell"])
            return [MarketOrder(side, self.cfg.forced_volume, best.agent_id, "forced")]

        # Zabezpieczenie przed zakleszczeniem: wszyscy w pozycji -> wymuś zamknięcie
        # agenta najbliższego któregoś z progów (SL lub TP).
        positioned = [a for a in self.agents if not a.is_flat]
        best = min(
            positioned,
            key=lambda a: min(abs(self.last_price - a.sl_price),
                              abs(self.last_price - a.tp_price)),
        )
        side = "sell" if best.position > 0 else "buy"
        return [MarketOrder(side, abs(best.position), best.agent_id, "forced_close")]

    # -- jedna tura --------------------------------------------------------- #
    def step(self) -> dict:
        # Krok 1: utrzymanie bufora.
        self.book.replenish(self.last_price)

        # Kroki 2-4: zgromadź zlecenia Market.
        orders = self._step_sl_tp()
        orders += self._step_entry_decisions()
        if not orders:
            orders += self._step_forced_action()

        # Krok 5: dopasowanie (matching engine).
        self.rng.shuffle(orders)
        trades: List[Trade] = []
        close_events: List[Tuple[int, str, float]] = []   # (agent, 'sl'|'tp', target)
        entry_fills: Dict[int, Tuple[float, str]] = {}    # agent -> (avg_price, side)

        for mo in orders:
            t, filled_limits = self.book.match(mo)
            trades.extend(t)
            avg = self._vwap(t)

            if mo.kind == "sl":
                if t:
                    close_events.append((mo.agent_id, "sl", self.agents[mo.agent_id].sl_price))
            elif mo.kind == "forced_close":
                if t:
                    a = self.agents[mo.agent_id]
                    d_sl = abs(self.last_price - a.sl_price)
                    d_tp = abs(self.last_price - a.tp_price)
                    if d_sl <= d_tp:
                        close_events.append((mo.agent_id, "sl", a.sl_price))
                    else:
                        close_events.append((mo.agent_id, "tp", a.tp_price))
            else:  # 'entry' | 'forced'
                if t and avg is not None:
                    entry_fills[mo.agent_id] = (avg, mo.side)

            for agent_id, price in filled_limits:
                close_events.append((agent_id, "tp", price))

        # Nowa cena = średnia ważona wolumenem transakcji tury.
        vwap = self._vwap(trades)
        if vwap is not None:
            self.last_price = vwap

        # Krok 6: adaptacja FV po zamknięciu pozycji (TP/SL).
        for agent_id, kind, target in close_events:
            a = self.agents[agent_id]
            self.book.cancel_limits(agent_id)
            a.close(target if a.entry_price is not None else self.last_price)
            a.adapt_after_close(target, self.rng, self.cfg)

        # Otwarcie nowych pozycji (SL/TP + limit TP do książki).
        for agent_id, (avg, side) in entry_fills.items():
            a = self.agents[agent_id]
            sl = max(1e-6, self.cfg.sl_pct + self.rng.uniform(-self.cfg.sl_noise, self.cfg.sl_noise))
            tp = max(1e-6, self.cfg.tp_pct + self.rng.uniform(-self.cfg.tp_noise, self.cfg.tp_noise))
            a.enter(avg, side, sl, tp)
            self.book.add_limit(agent_id, "sell" if side == "buy" else "buy", a.tp_price, abs(a.position))

        # Krok 7: statystyki.
        record = self._record(trades)
        self.history.append(record)
        return record

    def _record(self, trades: Sequence[Trade]) -> dict:
        volume = sum(t.volume for t in trades)
        fvs = [a.fv for a in self.agents]
        return {
            "turn": len(self.history),
            "price": self.last_price,
            "volume": volume,
            "n_trades": len(trades),
            "best_bid": self.book.best_bid(),
            "best_ask": self.book.best_ask(),
            "spread": (self.book.best_ask() or 0) - (self.book.best_bid() or 0),
            "open_interest": sum(abs(a.position) for a in self.agents),
            "mean_fv": sum(fvs) / len(fvs),
            "min_fv": min(fvs),
            "max_fv": max(fvs),
        }

    # -- przebieg ----------------------------------------------------------- #
    def run(self, verbose: bool = True) -> None:
        for _ in range(self.cfg.n_turns):
            rec = self.step()
            if verbose and rec["turn"] % max(1, self.cfg.n_turns // 10) == 0:
                print(
                    f"turn={rec['turn']:>6}  price={rec['price']:.4f}  "
                    f"vol={rec['volume']:>10.1f}  meanFV={rec['mean_fv']:.4f}"
                )


# --------------------------------------------------------------------------- #
# Analiza / wykresy / CSV
# --------------------------------------------------------------------------- #
def summarize(sim: Simulator) -> str:
    h = sim.history
    p0, p1 = h[0]["price"], h[-1]["price"]
    total_vol = sum(r["volume"] for r in h)
    fv0 = h[0]["mean_fv"]
    lines = [
        "=" * 62,
        "Podsumowanie symulacji",
        "=" * 62,
        f"agenty            : {sim.cfg.n_agents}",
        f"tury              : {sim.cfg.n_turns}",
        f"cena start/koniec : {p0:.4f} -> {p1:.4f}  ({(p1/p0-1)*100:+.2f}%)",
        f"min/max cena      : {min(r['price'] for r in h):.4f} / "
        f"{max(r['price'] for r in h):.4f}",
        f"śr. FV start/end  : {fv0:.4f} -> {h[-1]['mean_fv']:.4f}",
        f"łączny wolumen    : {total_vol:.1f}",
        f"łączna liczba tx  : {sum(r['n_trades'] for r in h)}",
        f"martwe tury       : {sum(1 for r in h if r['volume'] <= 0)}",
    ]
    return "\n".join(lines)


def write_csv(sim: Simulator, path: str) -> None:
    fields = list(sim.history[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sim.history)


def plot(sim: Simulator, save_path: Optional[str] = None) -> None:
    try:
        import matplotlib
        if save_path is not None:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[uwaga] matplotlib nie jest zainstalowany – pomijam wykres.")
        return

    h = sim.history
    turns = [r["turn"] for r in h]
    price = [r["price"] for r in h]
    mean_fv = [r["mean_fv"] for r in h]
    volume = [r["volume"] for r in h]
    spread = [r["spread"] for r in h]

    fig, axs = plt.subplots(4, 1, figsize=(11, 12), sharex=True)
    axs[0].plot(turns, price, lw=0.8, color="tab:blue", label="cena")
    axs[0].plot(turns, mean_fv, lw=0.8, color="tab:orange", label="śr. FV")
    axs[0].set_ylabel("Cena / FV")
    axs[0].legend()
    axs[0].set_title("Cena i konsensus FV")

    axs[1].fill_between(turns, volume, step="mid", color="tab:green", alpha=0.6)
    axs[1].set_ylabel("Wolumen tury")

    axs[2].plot(turns, spread, lw=0.6, color="tab:red")
    axs[2].set_ylabel("Spread")

    # rozrzut percepcji FV (p10-p90 w przybliżeniu przez min/max)
    axs[3].plot(turns, [r["max_fv"] - r["min_fv"] for r in h], lw=0.6, color="tab:purple")
    axs[3].set_ylabel("Rozpiętość FV")
    axs[3].set_xlabel("Tura")

    fig.tight_layout()
    if save_path is not None:
        fig.savefig(save_path, dpi=150)
        print(f"Wykres zapisano: {save_path}")
    else:
        plt.show()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Symulator rynku: bufor płynności + adaptacja FV")
    p.add_argument("--turns", type=int, default=2000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--price", type=float, default=100.0)
    p.add_argument("--tick", type=float, default=0.05)
    p.add_argument("--agents", type=int, default=300)
    p.add_argument("--levels", type=int, default=100)
    p.add_argument("--fv-spread", type=float, default=0.05)
    p.add_argument("--threshold", type=float, default=0.005)
    p.add_argument("--sl", type=float, default=0.008)
    p.add_argument("--tp", type=float, default=0.012)
    p.add_argument("--base-passive", type=float, default=1000.0)
    p.add_argument("--csv", type=str, default=None)
    p.add_argument("--plot", action="store_true")
    p.add_argument("--plot-save", type=str, default=None)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    cfg = Config(
        seed=args.seed,
        start_price=args.price,
        tick=args.tick,
        n_levels=args.levels,
        n_agents=args.agents,
        n_turns=args.turns,
        fv_spread=args.fv_spread,
        entry_threshold=args.threshold,
        sl_pct=args.sl,
        tp_pct=args.tp,
        base_passive=args.base_passive,
    )
    sim = Simulator(cfg)
    sim.run(verbose=not args.quiet)

    print(summarize(sim))
    if args.csv:
        write_csv(sim, args.csv)
        print(f"Historia zapisana: {args.csv}")
    if args.plot or args.plot_save:
        plot(sim, save_path=args.plot_save)


if __name__ == "__main__":
    main()
