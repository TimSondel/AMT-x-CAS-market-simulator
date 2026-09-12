"""Testy UI: oś czasu świec wolumenowych, wykres, pasek sterowania, okno."""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py  # noqa: E402
from PyQt5 import QtWidgets  # noqa: E402

from market_sim.config import load_config  # noqa: E402
from market_sim.core.order import Side, Trade  # noqa: E402
from market_sim.runner import run  # noqa: E402
from market_sim.storage import CandleBuilder  # noqa: E402
from market_sim.storage.aggregate import Candle  # noqa: E402
from market_sim.storage.writer import CANDLE_DTYPE, TRADE_DTYPE  # noqa: E402
from market_sim.ui.candle_chart import CandlestickItem, CandleChartWidget  # noqa: E402
from market_sim.ui.controls import ControlBar  # noqa: E402
from market_sim.ui.data_source import SimulationData  # noqa: E402
from market_sim.ui.main_window import MainWindow  # noqa: E402
from market_sim.ui.timeline import build_timeline  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def write_days(path, days: dict[int, list[Trade]]) -> None:
    """Zapisuje ręcznie przygotowane transakcje do pliku HDF5."""
    with h5py.File(path, "w") as handle:
        meta = handle.create_group("meta")
        meta.attrs["seed"] = 1
        meta.attrs["version"] = "1.0"
        meta.create_dataset("config_yaml", data="{}")
        index = []
        for day_index, trades in sorted(days.items()):
            group = handle.require_group("days").require_group(
                f"day_{day_index:04d}"
            )
            array = np.empty(len(trades), dtype=TRADE_DTYPE)
            for i, trade in enumerate(trades):
                array[i] = (
                    trade.timestamp,
                    trade.price,
                    trade.size,
                    int(trade.aggressor_side),
                    day_index,
                )
            group.create_dataset("trades", data=array)
            builder = CandleBuilder(target_volume=100, day_index=day_index)
            candles = builder.add_trades(trades)
            builder.finish()
            candle_array = np.empty(len(candles), dtype=CANDLE_DTYPE)
            for i, candle in enumerate(candles):
                candle_array[i] = (
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    candle.open_time,
                    candle.close_time,
                    day_index,
                )
            group.create_dataset("candles", data=candle_array)
            profile = group.create_group("profile")
            profile.create_dataset("prices", data=np.asarray([100.0]))
            profile.create_dataset("volumes", data=np.asarray([1], dtype="i8"))
            day_meta = group.create_group("meta")
            day_meta.attrs["open"] = trades[0].price if trades else np.nan
            day_meta.attrs["close"] = trades[-1].price if trades else np.nan
            day_meta.attrs["high"] = max((t.price for t in trades), default=np.nan)
            day_meta.attrs["low"] = min((t.price for t in trades), default=np.nan)
            day_meta.attrs["total_volume"] = sum(t.size for t in trades)
            day_meta.attrs["num_trades"] = len(trades)
            index.append((day_index, 100.0, 100.0))
        handle.create_dataset("index", data=np.asarray(index, dtype="f8"))


@pytest.fixture
def two_day_file(workspace_tmp):
    """Plik z dwoma dniami; dzień 0 ma świece 100/100/60 wolumenu."""
    days = {
        0: [
            Trade(1.0, 100.0, 60, Side.BUY),
            Trade(2.0, 100.25, 40, Side.BUY),
            Trade(3.0, 100.5, 100, Side.SELL),
            Trade(4.0, 100.75, 60, Side.BUY),
        ],
        1: [
            Trade(1.0, 101.0, 100, Side.BUY),
            Trade(2.0, 101.25, 50, Side.SELL),
        ],
    }
    path = workspace_tmp / "two_days.h5"
    write_days(path, days)
    return path


@pytest.fixture
def saved_file(config, workspace_tmp, qt_app):
    """Zapis wygenerowany przez symulację (2 dni, mały wolumen świecy)."""
    short = config.model_copy(deep=True)
    short.simulation.turns_per_day = 300
    short.volume.candle_target_volume = 50
    output = workspace_tmp / "ui.h5"
    run(config=short, days=2, seed=17, output=output)
    return output


# ----------------------------------------------------------------------
# oś czasu (wolumen, nie kroki)
# ----------------------------------------------------------------------
def test_timeline_swiece_maja_dokladny_wolumen(two_day_file):
    data = SimulationData(two_day_file)

    timeline = data.timeline(100)

    assert timeline.candle_count == 5  # dzień 0: 100+100+60, dzień 1: 100+50
    assert [candle.volume for candle in timeline.candles] == [100, 100, 60, 100, 50]
    assert timeline.spans[0].end_candle == 2
    assert timeline.spans[1].start_candle == 3


def test_timeline_domkniecie_swiecy_po_pelnym_wolumenie(two_day_file):
    data = SimulationData(two_day_file)
    timeline = data.timeline(100)

    # po 1. transakcji: świeca 0, 60/100
    index, closed = timeline.state_at(1)
    assert (index, closed) == (0, False)

    # po 2. transakcji: świeca 0 domknięta dokładnie wolumenem
    index, closed = timeline.state_at(2)
    assert (index, closed) == (0, True)
    assert timeline.candles[0].volume == 100

    # po 3. transakcji (100 szt.): świeca 1 domknięta
    index, closed = timeline.state_at(3)
    assert (index, closed) == (1, True)


def test_timeline_domkniecie_przed_ostatnia_transakcja(two_day_file):
    data = SimulationData(two_day_file)
    timeline = data.timeline(100)

    index, closed = timeline.state_at(4)
    assert (index, closed) == (2, False)  # 60/100, w budowie


def test_timeline_granice_dni(two_day_file):
    data = SimulationData(two_day_file)
    timeline = data.timeline(100)

    assert len(timeline.spans) == 2
    assert timeline.spans[0].start_candle == 0
    assert timeline.spans[0].end_candle == 2
    assert timeline.spans[1].start_candle == 3
    assert timeline.day_boundaries() == [(3, 1)]
    assert timeline.day_start_trade(1) == 4


def test_timeline_start_pusty(two_day_file):
    data = SimulationData(two_day_file)
    timeline = data.timeline(100)

    index, closed = timeline.state_at(0)

    assert index == 0
    assert closed is False


# ----------------------------------------------------------------------
# element świec
# ----------------------------------------------------------------------
def test_candlestick_item_bounding_rect():
    item = CandlestickItem(
        [Candle(open=100.0, high=101.0, low=99.0, close=100.5, volume=100)]
    )

    rect = item.boundingRect()
    assert rect.top() == pytest.approx(99.0)
    assert rect.bottom() == pytest.approx(101.0)
    assert rect.width() > 0


def test_candlestick_item_bez_danych():
    item = CandlestickItem()

    assert item.boundingRect().isEmpty()
    item.set_candles(
        [Candle(open=1.0, high=1.0, low=1.0, close=1.0, volume=1)]
    )
    assert not item.boundingRect().isEmpty()
    item.set_candles([])
    assert item.boundingRect().isEmpty()


# ----------------------------------------------------------------------
# wykres
# ----------------------------------------------------------------------
def test_wykres_startuje_pusty(qt_app):
    chart = CandleChartWidget()
    chart.set_series(
        [Candle(open=100.0, high=101.0, low=99.0, close=100.5, volume=100)]
    )

    chart.rewind()

    assert chart.current_index == -1
    assert chart.drawn_candles == []
    assert chart.total_count == 1


def test_wykres_rysuje_swiece_w_budowie(qt_app):
    chart = CandleChartWidget()
    partial = Candle(
        open=100.0, high=100.5, low=99.5, close=100.2, volume=40,
        open_time=0.0, close_time=1.0,
    )
    chart.set_series(
        [
            Candle(open=100.0, high=100.5, low=99.5, close=100.2, volume=100),
            Candle(open=100.2, high=100.8, low=100.0, close=100.6, volume=70),
        ]
    )

    chart.update_candles(0, False, 100.2, partial)

    drawn = chart.drawn_candles
    assert len(drawn) == 1
    assert drawn[0].volume == 40  # świeca częściowa, nie docelowa
    assert chart.marker_text.isVisible()


def test_wykres_swieca_w_budowie_rosnie(qt_app):
    chart = CandleChartWidget()
    chart.set_series(
        [Candle(open=100.0, high=100.5, low=99.5, close=100.2, volume=100)]
    )

    for volume in (10, 45, 99):
        partial = Candle(
            open=100.0, high=100.5, low=99.5, close=100.2, volume=volume
        )
        chart.update_candles(0, False, 100.2, partial)
        assert chart.drawn_candles[0].volume == volume


def test_wykres_dorysowuje_kolejne_swiece(qt_app):
    chart = CandleChartWidget()
    chart.set_series(
        [
            Candle(open=100.0, high=100.5, low=99.5, close=100.2, volume=100),
            Candle(open=100.2, high=100.8, low=100.0, close=100.6, volume=70),
        ]
    )
    partial = Candle(
        open=100.0, high=100.5, low=99.5, close=100.2, volume=30
    )

    chart.update_candles(0, False, 100.2, partial)
    assert len(chart.drawn_candles) == 1
    assert chart.drawn_candles[0].volume == 30

    chart.update_candles(1, True, 100.6)
    drawn = chart.drawn_candles
    assert len(drawn) == 2
    assert [candle.volume for candle in drawn] == [100, 70]


def test_wykres_separatory_dni(qt_app):
    data_spans = build_timeline(
        [
            (0, [Trade(1.0, 100.0, 100, Side.BUY)]),
            (1, [Trade(1.0, 101.0, 100, Side.BUY)]),
        ],
        100,
    )
    chart = CandleChartWidget()
    chart.set_series(data_spans.candles, data_spans.spans)

    assert len(chart.separators) == 1


def test_wykres_bez_danych(qt_app):
    chart = CandleChartWidget()

    chart.set_series([])

    assert chart.total_count == 0
    assert not chart.message.isHidden()


# ----------------------------------------------------------------------
# pasek sterowania
# ----------------------------------------------------------------------
def test_pasek_sterowania_zakres_dni(qt_app):
    bar = ControlBar()

    bar.set_days([0, 1, 2])

    assert bar.day_spin.maximum() == 2
    assert bar.day_spin.isEnabled()
    assert bar.day_total.text() == "/ 2"


def test_pasek_sterowania_emituje_zmiane_dnia(qt_app):
    bar = ControlBar()
    bar.set_days([0, 1, 2])
    received: list[int] = []
    bar.day_changed.connect(received.append)

    bar.day_spin.setValue(2)

    assert received == [2]


def test_pasek_sterowania_zakres_transakcji(qt_app):
    bar = ControlBar()

    bar.set_range(7)

    assert bar.slider.maximum() == 7
    assert bar.slider.value() == 0
    assert bar.progress.text() == "0 / 7"


def test_pasek_sterowania_progress_zatrzymuje_play(qt_app):
    bar = ControlBar()
    bar.set_range(5)
    bar.set_playing(True)
    states: list[bool] = []
    bar.play_toggled.connect(states.append)

    bar.set_progress(5, 5)

    assert bar.is_playing() is False
    assert states[-1] is False


def test_pasek_sterowania_wolumen_swiecy(qt_app):
    bar = ControlBar()
    received: list[int] = []
    bar.volume_changed.connect(received.append)

    bar.volume_spin.setValue(250)

    assert bar.candle_volume() == 250
    assert received == [250]


def test_pasek_sterowania_predkosc(qt_app):
    bar = ControlBar()
    bar.speed_box.setCurrentText("1x")
    slow = bar.trades_per_tick()
    bar.speed_box.setCurrentText("8x")

    assert bar.trades_per_tick() > slow


def test_pasek_sterowania_autoscale_i_follow(qt_app):
    bar = ControlBar()

    assert bar.is_autoscale() is True
    assert bar.is_follow() is True

    bar.autoscale_button.setChecked(False)
    bar.follow_button.setChecked(False)

    assert bar.is_autoscale() is False
    assert bar.is_follow() is False


# ----------------------------------------------------------------------
# okno główne
# ----------------------------------------------------------------------
def test_okno_glowne_wczytuje_os_czasu(two_day_file, qt_app):
    window = MainWindow(data_path=two_day_file)

    try:
        assert window.timeline is not None
        assert window.total_trades == 6
        assert window.position == 0
        assert window.chart.total_count == 5
        assert window.chart.current_index == -1  # nic nie narysowane na starcie
        assert window.control_bar.day_spin.maximum() == 1
        assert window.chart.total_count == 5
        assert window.chart.current_index == -1
    finally:
        window.close()


def test_okno_glowne_krok_odslania_swiece_stopniowo(two_day_file, qt_app):
    window = MainWindow(data_path=two_day_file)
    try:
        window._on_step()
        assert window.position == 1
        # świeca w budowie: tylko pierwsza transakcja (60 szt.)
        assert len(window.chart.drawn_candles) == 1
        assert window.chart.drawn_candles[0].volume == 60

        window._on_step()  # 60 + 40 = 100 -> świeca domknięta
        assert window.chart.current_index == 0
        assert len(window.chart.drawn_candles) >= 1
        assert window.chart.drawn_candles[0].volume == 100

        window._on_step()  # 100 szt. -> kolejna świeca
        assert window.chart.current_index == 1
        assert window.chart.drawn_candles[1].volume == 100
    finally:
        window.close()


def test_okno_glowne_play_odtwarza_transakcje(two_day_file, qt_app):
    window = MainWindow(data_path=two_day_file)
    try:
        window.control_bar.play_button.setChecked(True)
        assert window._timer.isActive()
        assert window.position == 0

        window._on_tick()
        assert window.position == window.control_bar.trades_per_tick()
        assert len(window.chart.drawn_candles) >= 1

        window._on_tick()
        window.control_bar.play_button.setChecked(False)
        assert not window._timer.isActive()
    finally:
        window.close()


def test_okno_glowne_play_do_konca_zatrzymuje(two_day_file, qt_app):
    window = MainWindow(data_path=two_day_file)
    try:
        window.control_bar.play_button.setChecked(True)
        for _ in range(10):
            window._on_tick()

        assert window.position == window.total_trades
        assert window.control_bar.is_playing() is False
        assert not window._timer.isActive()
    finally:
        window.close()


def test_okno_glowne_przelaczanie_dni(two_day_file, qt_app):
    window = MainWindow(data_path=two_day_file)
    try:
        window._on_day_changed(1)
        # pierwsza świeca dnia 1 (transakcja domykająca dzień 0 to pozycja 4)
        assert window.position == 5
        assert window.control_bar.day_spin.value() == 1
        assert window.chart.current_index == 3

        window._on_day_changed(0)
        assert window.position == 0
        assert window.chart.current_index == -1
    finally:
        window.close()


def test_okno_glowne_play_od_nowa_po_zakonczeniu(two_day_file, qt_app):
    window = MainWindow(data_path=two_day_file)
    try:
        window._set_position(window.total_trades)
        assert window.position == window.total_trades

        window.control_bar.play_button.setChecked(True)

        assert window.position == 0
        assert window._timer.isActive()
    finally:
        window.close()


def test_okno_glowne_zmiana_wolumenu_swiecy(two_day_file, qt_app):
    window = MainWindow(data_path=two_day_file)
    try:
        before = window.chart.total_count
        window.control_bar.volume_spin.setValue(50)

        assert window.chart.total_count > before
        assert window.position == 0
        assert window.chart.current_index == -1
    finally:
        window.close()


def test_okno_glowne_status_zawiera_wolumen(two_day_file, qt_app):
    window = MainWindow(data_path=two_day_file)
    try:
        window._set_position(1)

        text = window.metrics.text()
        assert "Dzień 0/1" in text
        assert "w budowie" in text
        assert "wolumen świecy 60/100" in text
    finally:
        window.close()


def test_okno_glowne_bez_danych(qt_app):
    window = MainWindow()

    try:
        if window.data is None:
            assert "Brak danych" in window.metrics.text()
    finally:
        window.close()


def test_okno_glowne_wiele_dni_z_symulacji(saved_file, qt_app):
    window = MainWindow(data_path=saved_file)
    try:
        assert window.timeline is not None
        assert len(window.timeline.spans) == 2
        assert window.timeline.spans[1].start_candle > 0
        assert window.total_trades > 0
    finally:
        window.close()


def test_generowanie_zapisu_z_okna(config, workspace_tmp, qt_app, monkeypatch):
    import market_sim.ui.main_window as main_window

    short = config.model_copy(deep=True)
    short.simulation.turns_per_day = 150
    output = workspace_tmp / "generated.h5"
    monkeypatch.setattr(main_window, "DEFAULT_OUTPUT", output)
    monkeypatch.setattr(main_window, "load_config", lambda: short)
    monkeypatch.setattr(
        main_window.QtWidgets.QInputDialog,
        "getInt",
        staticmethod(lambda *a, **k: (2, True)),
    )

    window = MainWindow()
    try:
        window.generate_default()
        assert output.exists()
        assert window.data is not None
        assert len(window.timeline.spans) == 2
    finally:
        window.close()
