"""Testy symulacji: procesy, wolumen, dzień, EOD i zapis HDF5."""

from __future__ import annotations

import math
import random

import pytest

from market_sim.config import Config, load_config
from market_sim.core.order import Side
from market_sim.core.perception import (
    long_probability,
    normal_cdf,
    sample_offset,
    sample_order_size,
    sigmoid,
)
from market_sim.core.processes import (
    CertaintyProcess,
    FairValueProcess,
    JumpDiffusion,
    OrnsteinUhlenbeck,
    RegimeProcess,
    SpreadProcess,
    VisibilityProcess,
)
from market_sim.core.volume import (
    intraday_multiplier,
    is_overnight,
    volume_curve,
)
from market_sim.runner import generate_day, run
from market_sim.simulation import PositionLedger, Simulation
from market_sim.storage import CandleBuilder, HDF5Reader, build_volume_profile


# ----------------------------------------------------------------------
# konfiguracja
# ----------------------------------------------------------------------
def test_config_wczytuje_wszystkie_sekcje(config):
    assert config.simulation.tick_size > 0
    assert config.simulation.turns_per_day > 0
    assert config.orderbook.partial_fill_mode in ("cancel", "synthetic", "panic")
    assert config.perception.num_regimes == 3
    assert config.spread.min_ticks <= config.spread.max_ticks
    assert 0 <= config.volume.overnight_activity <= 1
    assert config.volume.candle_target_volume > 0


def test_config_odrzuca_zly_tick():
    with pytest.raises(Exception):
        Config.model_validate(
            {
                "simulation": {
                    "tick_size": 0,
                    "step_seconds": 1,
                    "turns_per_day": 10,
                    "days_to_generate": 1,
                },
                "orderbook": {
                    "min_depth_ticks": 5,
                    "refill_decay_tau": 1,
                    "partial_fill_mode": "synthetic",
                },
                "perception": {
                    "ou_theta": 0.1,
                    "ou_sigma_quiet": 0.001,
                    "ou_sigma_normal": 0.005,
                    "ou_sigma_volatile": 0.02,
                    "jump_lambda": 0.01,
                    "jump_mu": 0.0,
                    "jump_sigma": 0.02,
                    "regime_transition_matrix": [[0.9, 0.1], [0.1, 0.9]],
                },
                "spread": {
                    "log_ou_mu": 0.7,
                    "log_ou_theta": 0.1,
                    "log_ou_sigma": 0.1,
                    "min_ticks": 1,
                    "max_ticks": 20,
                },
                "certainty": {
                    "log_ou_mu": 0.0,
                    "log_ou_theta": 0.05,
                    "log_ou_sigma": 0.1,
                    "clip_min": 0.3,
                    "clip_max": 2.5,
                },
                "visibility": {"beta_alpha": 2.0, "beta_beta": 2.0, "market_bias": 0.7},
                "volume": {
                    "open_peak_amp": 3.0,
                    "open_peak_sigma": 0.05,
                    "close_peak_amp": 2.5,
                    "close_peak_sigma": 0.05,
                    "midday_baseline": 0.5,
                    "overnight_factor": 0.1,
                    "overnight_activity": 0.3,
                },
                "eod": {
                    "close_pct": 0.7,
                    "limit_levels": 5,
                    "limit_size": 500,
                    "keep_ticks": 20,
                },
            }
        )


# ----------------------------------------------------------------------
# procesy (functions.md 1-5)
# ----------------------------------------------------------------------
def test_ou_zmierza_do_sredniej():
    process = OrnsteinUhlenbeck(theta=0.5, mu=10.0, sigma=0.0, rng=random.Random(1))
    process.value = 0.0

    for _ in range(100):
        process.step(0.25)

    assert process.value == pytest.approx(10.0, rel=0.05)


def test_ou_reaguje_na_sigma():
    quiet = OrnsteinUhlenbeck(0.05, 0.0, 0.001, random.Random(2))
    volatile = OrnsteinUhlenbeck(0.05, 0.0, 0.5, random.Random(2))
    for _ in range(200):
        quiet.step(0.25)
        volatile.step(0.25)
    assert abs(volatile.value) > abs(quiet.value)


def test_jump_diffusion_generuje_skoki():
    jumps = JumpDiffusion(lam=10.0, jump_mu=0.0, jump_sigma=0.1, rng=random.Random(3))
    total = sum(1 for _ in range(1000) if jumps.step(0.25))
    assert total > 0


def test_regime_process_zmienia_stan():
    matrix = [[0.5, 0.5], [0.5, 0.5]]
    regime = RegimeProcess(matrix, random.Random(4))
    states = {regime.maybe_transition() for _ in range(200)}
    assert states == {0, 1}


def test_fair_value_jump_i_regime_zmieniaja_wartosc(config):
    process = FairValueProcess(config, random.Random(5), start_value=100.0)
    values = [process.update(0.25) for _ in range(500)]
    assert len(set(values)) > 1
    assert 0 < process.regime.state < process.regime.num_states


def test_spread_utrzymuje_sie_w_zakresie(config):
    spread = SpreadProcess(config, random.Random(6))
    for _ in range(1000):
        ticks = spread.update(0.25, config.tick_size)
        assert config.spread.min_ticks <= ticks <= config.spread.max_ticks


def test_certainty_jest_przycieta(config):
    certainty = CertaintyProcess(config, random.Random(7))
    for _ in range(1000):
        value = certainty.update(0.25)
        assert config.certainty.clip_min <= value <= config.certainty.clip_max


def test_certainty_zmienia_wartosc_w_czasie(config):
    certainty = CertaintyProcess(config, random.Random(8))
    values = {round(certainty.update(0.25), 6) for _ in range(100)}
    assert len(values) > 1


def test_visibility_zmienia_sie_i_jest_w_zakresie(config):
    visibility = VisibilityProcess(config, random.Random(9))
    values = [visibility.update(1.0, config.certainty.clip_max) for _ in range(200)]
    assert all(0.0 <= value <= 1.0 for value in values)
    assert len(set(round(v, 6) for v in values)) > 1
    assert 0.0 <= visibility.market_weight() <= 1.0


def test_wysoka_pewnosc_zwieksza_wage_marketowa(config):
    visibility = VisibilityProcess(config, random.Random(10))
    low = visibility.update(0.4, config.certainty.clip_max)
    visibility_low = VisibilityProcess(config, random.Random(10))
    visibility_low.update(0.4, config.certainty.clip_max)
    high = visibility_low.update(2.4, config.certainty.clip_max)
    assert high >= low


# ----------------------------------------------------------------------
# percepcja i wolumen (functions.md 2, 6, 7)
# ----------------------------------------------------------------------
def test_sigmoid_i_normal_cdf():
    assert sigmoid(0.0) == pytest.approx(0.5)
    assert normal_cdf(0.0) == pytest.approx(0.5)
    assert 0.0 < sigmoid(-5.0) < 0.5 < sigmoid(5.0) < 1.0


def test_p_long_rosnie_z_odchyleniem_ceny():
    below = long_probability(center=101.0, mid=100.0, spread=1.0, certainty=1.0, tick=0.25)
    above = long_probability(center=99.0, mid=100.0, spread=1.0, certainty=1.0, tick=0.25)
    assert below > 0.5 > above
    assert long_probability(100.0, 100.0, 1.0, 1.0, 0.25) == pytest.approx(0.5)


def test_pewnosc_wzmacnia_sygnal():
    weak = long_probability(101.0, 100.0, 1.0, 0.3, 0.25)
    strong = long_probability(101.0, 100.0, 1.0, 2.5, 0.25)
    assert strong > weak


def test_sample_order_size_jest_dodatni(config):
    rng = random.Random(11)
    sizes = [sample_order_size(config, rng) for _ in range(500)]
    assert all(size >= 1 for size in sizes)
    assert len(set(sizes)) > 1


def test_sample_offset_co_najmniej_jeden_tick():
    rng = random.Random(12)
    offsets = [sample_offset(0.0, 1.0, rng) for _ in range(50)]
    assert all(offset >= 1 for offset in offsets)


def test_volume_curve_ma_kształt_u():
    start = volume_curve(0.0)
    middle = volume_curve(0.5)
    end = volume_curve(1.0)
    assert start > middle
    assert end > middle


def test_intraday_multiplier_i_overnight(config):
    assert intraday_multiplier(config, 1) > 0
    assert is_overnight(config, 1) is False

    night_config = config.model_copy(deep=True)
    night_config.simulation.day_start = 0
    night_config.simulation.day_end = 100
    assert is_overnight(night_config, 500) is True
    assert is_overnight(night_config, 50) is False


# ----------------------------------------------------------------------
# krok symulacji (logic.md 1.5)
# ----------------------------------------------------------------------
def test_simulation_inicjalizuje_book(config):
    simulation = Simulation(config, seed=1)
    assert simulation.orderbook.best_bid is None
    assert simulation.orderbook.mid_price() == config.simulation.start_price


def test_step_generuje_transakcje_i_aktualizuje_parametry(config):
    simulation = Simulation(config, seed=7)
    simulation.orderbook.refill(min_ticks=config.orderbook.min_depth_ticks)
    fv_values = []
    for turn in range(1, 301):
        simulation.step(turn)
        fv_values.append(simulation.fair_value.value)

    assert simulation.stats.market_orders > 0
    assert simulation.stats.limit_orders > 0
    assert simulation.stats.num_trades > 0
    assert simulation.stats.total_volume > 0
    assert len(set(round(v, 9) for v in fv_values)) > 1
    assert simulation.orderbook.best_bid < simulation.orderbook.best_ask


def test_step_market_zapisuje_pozycje_w_ledgerze(config):
    simulation = Simulation(config, seed=3)
    simulation.orderbook.refill(min_ticks=config.orderbook.min_depth_ticks)
    for turn in range(1, 201):
        simulation.step(turn)

    assert simulation.ledger.open_count == simulation.stats.num_trades
    assert simulation.ledger.open_volume == simulation.stats.total_volume


def test_step_zerowy_wolumen_nie_generuje_zlecen(config):
    quiet = config.model_copy(deep=True)
    quiet.volume.overnight_factor = 0.0
    quiet.volume.overnight_activity = 0.0
    quiet.simulation.day_start = 0
    quiet.simulation.day_end = 10
    simulation = Simulation(quiet, seed=4)
    simulation.orderbook.refill(min_ticks=quiet.orderbook.min_depth_ticks)

    simulation.step(100)

    assert simulation.stats.market_orders == 0
    assert simulation.stats.limit_orders == 0
    assert simulation.stats.silent_steps == 1


def test_snapshot_jest_emitowany_kazdy_krok(config):
    simulation = Simulation(config, seed=5)
    simulation.orderbook.refill(min_ticks=config.orderbook.min_depth_ticks)
    for turn in range(1, 51):
        simulation.step(turn)
    assert len(simulation.snapshots) == 50
    time, mid, bid, ask = simulation.snapshots[-1]
    assert time > 0 and mid > 0 and bid < ask


def test_brak_przeciętego_booka_w_trakcie_krokow(config):
    simulation = Simulation(config, seed=6)
    simulation.orderbook.refill(min_ticks=config.orderbook.min_depth_ticks)
    for turn in range(1, 501):
        simulation.step(turn)
        bid = simulation.orderbook.best_bid
        ask = simulation.orderbook.best_ask
        if bid is not None and ask is not None:
            assert bid < ask


# ----------------------------------------------------------------------
# dzień i EOD (logic.md 1.6-1.7)
# ----------------------------------------------------------------------
def test_run_day_zwraca_komplet_metryk(config):
    short = config.model_copy(deep=True)
    short.simulation.turns_per_day = 300
    simulation = Simulation(short, seed=11)

    day = simulation.run_day(0)

    assert day.day_index == 0
    assert day.open_price == pytest.approx(short.simulation.start_price)
    assert day.num_trades > 0
    assert day.total_volume > 0
    assert day.high >= day.low
    assert len(day.snapshots) > 0
    assert len(day.regime_path) == short.simulation.turns_per_day
    assert set(day.regime_path) <= {0, 1, 2}
    assert day.open_positions_after <= day.num_trades


def test_run_day_deterministyczny_dla_tego_samego_seeda(config):
    short = config.model_copy(deep=True)
    short.simulation.turns_per_day = 200

    first = Simulation(short, seed=99).run_day(0)
    second = Simulation(short, seed=99).run_day(0)

    assert first.num_trades == second.num_trades
    assert first.total_volume == second.total_volume
    assert first.close_price == second.close_price
    assert [t.price for t in first.trades] == [t.price for t in second.trades]


def test_rozne_seedy_daja_rozne_wyniki(config):
    short = config.model_copy(deep=True)
    short.simulation.turns_per_day = 200

    first = Simulation(short, seed=1).run_day(0)
    second = Simulation(short, seed=2).run_day(0)

    assert (first.num_trades, first.close_price) != (second.num_trades, second.close_price)


def test_eod_zamyka_czesc_pozycji_i_scina_book(config):
    short = config.model_copy(deep=True)
    short.simulation.turns_per_day = 200
    simulation = Simulation(short, seed=21)
    simulation.run_day(0)
    before = simulation.ledger.open_count

    closed = simulation.close_open_positions(0.7)

    assert closed > 0
    assert simulation.ledger.closed_count >= closed
    assert before > 0
    # procedura EOD nie zostawia bałaganu: brak zleceń z tagiem eod
    for book in (simulation.orderbook.bids, simulation.orderbook.asks):
        for level in book.values():
            assert all(order.tag != "eod" for order in level.orders)
    assert simulation.orderbook.best_bid < simulation.orderbook.best_ask


def test_eod_pct_zero_nic_nie_zamyka(small_config):
    simulation = Simulation(small_config, seed=22)
    simulation.run_day(0)
    assert simulation.close_open_positions(0.0) == 0


def test_eod_bez_pozycji_nie_zawodzi(small_config):
    simulation = Simulation(small_config, seed=23)
    assert simulation.close_open_positions(0.7) == 0


def test_thin_to_i_remove_tagged(config):
    simulation = Simulation(config, seed=24)
    simulation.orderbook.refill(min_ticks=20)
    assert len(simulation.orderbook.bids) == 20

    simulation.orderbook.thin_to(ticks=5)
    assert len(simulation.orderbook.bids) == 5
    assert len(simulation.orderbook.asks) == 5


def test_remove_tagged_usowa_wskazane_zlecenia(config):
    simulation = Simulation(config, seed=25)
    simulation.orderbook.refill(min_ticks=5)
    removed = simulation.orderbook.remove_tagged("refill")

    assert removed > 0
    assert len(simulation.orderbook.bids) == 0
    assert len(simulation.orderbook.asks) == 0


def test_ledger_rejestruje_i_zamyka_pozycje():
    from market_sim.core.order import Trade

    ledger = PositionLedger()
    ledger.register_trade(Trade(1.0, 100.0, 5, Side.BUY))
    ledger.register_trade(Trade(2.0, 100.25, 3, Side.SELL))
    assert ledger.open_count == 2
    assert ledger.open_volume == 8
    ledger.mark_closed(0)
    assert ledger.closed_count == 1
    assert ledger.open_count == 1
    assert ledger.open_volume == 3
    with pytest.raises(ValueError):
        ledger.mark_closed(-1)
    with pytest.raises(ValueError):
        ledger.mark_closed(5)


# ----------------------------------------------------------------------
# agregacja i zapis (ui.md 2.4)
# ----------------------------------------------------------------------
def test_candle_builder_respektuje_target_volume():
    from market_sim.core.order import Trade

    builder = CandleBuilder(target_volume=100)
    trades = [Trade(i * 1.0, 100.0 + i * 0.25, 40, Side.BUY) for i in range(10)]
    candles = builder.add_trades(trades)
    candles = builder.finish()

    assert len(candles) == 4
    assert all(candle.volume == 100 for candle in candles[:-1])
    assert sum(candle.volume for candle in candles) == 400


def test_candle_builder_rozbija_duza_transakcje():
    from market_sim.core.order import Trade

    builder = CandleBuilder(target_volume=10)
    builder.add_trade(Trade(1.0, 100.0, 25, Side.BUY))
    candles = builder.finish()

    assert len(candles) == 3
    assert [candle.volume for candle in candles] == [10, 10, 5]


def test_build_volume_profile_poc_vah_val():
    from market_sim.core.order import Trade

    trades = [
        Trade(1.0, 100.0, 10, Side.BUY),
        Trade(2.0, 100.25, 500, Side.BUY),
        Trade(3.0, 100.5, 10, Side.BUY),
        Trade(4.0, 100.75, 40, Side.BUY),
    ]
    profile = build_volume_profile(trades, tick_size=0.25, day_index=0)

    assert profile.prices == [100.0, 100.25, 100.5, 100.75]
    assert profile.volumes == [10, 500, 10, 40]
    assert profile.poc == 100.25
    # value area 70% = 392 -> POC 500 już ją pokrywa
    assert profile.val == 100.25
    assert profile.vah == 100.25
    assert profile.val <= profile.poc <= profile.vah


def test_build_volume_profile_pusty():
    profile = build_volume_profile([], tick_size=0.25)
    assert profile.poc is None
    assert profile.total_volume == 0


def test_volume_profile_rozszerza_value_area():
    from market_sim.core.order import Trade

    trades = [
        Trade(1.0, 100.0, 60, Side.BUY),
        Trade(2.0, 100.25, 40, Side.BUY),
        Trade(3.0, 100.5, 30, Side.BUY),
        Trade(4.0, 100.75, 70, Side.BUY),
    ]
    profile = build_volume_profile(
        trades, tick_size=0.25, value_area=0.7
    )

    # target 70% z 200 = 140; POC=70, najbliższy sąsiad (30) to 100.5,
    # potem 40 z 100.25 -> 140 pokryte, VAL=100.25
    assert profile.poc == 100.75
    assert profile.vah == 100.75
    assert profile.val == 100.25


def test_generate_day_buduje_swiece_i_profil(config):
    short = config.model_copy(deep=True)
    short.simulation.turns_per_day = 300
    simulation = Simulation(short, seed=31)

    day, candles, profile = generate_day(simulation, 0)

    assert day.num_trades > 0
    assert len(candles) > 0
    assert profile.total_volume > 0
    assert sum(candle.volume for candle in candles) == day.total_volume


def test_run_zapisuje_i_odczytuje_hdf5(config, workspace_tmp):
    short = config.model_copy(deep=True)
    short.simulation.turns_per_day = 200
    output = workspace_tmp / "simulation.h5"

    result = run(config=short, days=1, seed=42, output=output)
    assert output.exists()

    reader = HDF5Reader(output)
    meta = reader.meta()
    assert meta["seed"] == 42
    assert meta["version"] == "1.0"
    assert b"turns_per_day" in bytes(meta["config_yaml"]) or "turns_per_day" in str(
        meta["config_yaml"]
    )

    days = reader.days()
    assert days == ["day_0000"]
    payload = reader.day(0)
    assert payload["trades"].shape[0] == result["days"][0].num_trades
    assert payload["candles"].shape[0] > 0
    assert payload["prices"].shape[0] == payload["volumes"].shape[0]
    assert payload["prices"].shape[0] > 0
    assert payload["meta"]["total_volume"] == result["days"][0].total_volume

    index = reader.index()
    assert index.shape[1] == 3
    assert index[0][0] == 0


def test_run_generuje_wiele_dni(config, workspace_tmp):
    short = config.model_copy(deep=True)
    short.simulation.turns_per_day = 100
    output = workspace_tmp / "multi.h5"

    result = run(config=short, days=3, seed=5, output=output)

    assert len(result["days"]) == 3
    reader = HDF5Reader(output)
    assert reader.days() == ["day_0000", "day_0001", "day_0002"]


def test_kolejne_dni_nie_kumuluja_pozycji(config):
    short = config.model_copy(deep=True)
    short.simulation.turns_per_day = 300
    simulation = Simulation(short, seed=13)

    positions = [simulation.run_day(day).open_positions_after for day in range(3)]

    # 70% pozycji zamykane na koniec dnia -> liczba otwartych nie rośnie liniowo
    assert positions[2] < positions[0] + positions[1]
    assert simulation.ledger.closed_count > 0
