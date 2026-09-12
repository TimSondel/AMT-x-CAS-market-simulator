"""Okno główne aplikacji: menu, pasek sterowania, wykres świec, status."""

from __future__ import annotations

from pathlib import Path

from PyQt5 import QtCore, QtWidgets

from market_sim.config import load_config
from market_sim.runner import run
from market_sim.ui.candle_chart import CandleChartWidget
from market_sim.ui.controls import ControlBar
from market_sim.ui.data_source import SimulationData
from market_sim.ui.timeline import DaySpan, Timeline

DEFAULT_INPUT = Path("data/simulation.h5")
DEFAULT_OUTPUT = Path("data/simulation.h5")


class MainWindow(QtWidgets.QMainWindow):
    """Główne okno: oś czasu świec wolumenowych i odtwarzanie transakcji."""

    def __init__(self, data_path: str | Path | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AMT x CAS — symulator rynku")
        self.resize(1240, 760)

        self.data: SimulationData | None = None
        self.timeline: Timeline | None = None
        self.position = 0
        self._jump = -1
        self._fitted_day: int | None = None

        self.control_bar = ControlBar()
        self.chart = CandleChartWidget()

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.control_bar)
        layout.addWidget(self.chart)
        self.setCentralWidget(central)

        self.metrics = QtWidgets.QLabel("Brak danych")
        self.file_label = QtWidgets.QLabel("—")
        self.statusBar().addWidget(self.metrics, 1)
        self.statusBar().addPermanentWidget(self.file_label)

        self._build_menu()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(self.control_bar.interval_ms())
        self._timer.timeout.connect(self._on_tick)
        self._connect_signals()

        if data_path is not None:
            self.load_file(data_path)
        elif DEFAULT_INPUT.exists():
            self.load_file(DEFAULT_INPUT)

    # ------------------------------------------------------------------
    # budowa okna
    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Plik")
        open_action = file_menu.addAction("Otwórz…")
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._choose_file)
        generate_action = file_menu.addAction("Generuj nowy zapis…")
        generate_action.triggered.connect(self.generate_default)
        file_menu.addSeparator()
        quit_action = file_menu.addAction("Zakończ")
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)

        simulation_menu = self.menuBar().addMenu("Symulacja")
        play_action = simulation_menu.addAction("Play / pauza")
        play_action.setShortcut("Space")
        play_action.triggered.connect(lambda: self.control_bar.play_button.toggle())
        step_action = simulation_menu.addAction("Krok (jedna transakcja)")
        step_action.setShortcut("Right")
        step_action.triggered.connect(self._on_step)
        rewind_action = simulation_menu.addAction("Od początku")
        rewind_action.setShortcut("Ctrl+R")
        rewind_action.triggered.connect(self._rewind)
        next_day = simulation_menu.addAction("Następny dzień")
        next_day.setShortcut("Ctrl+Right")
        next_day.triggered.connect(lambda: self._step_day(1))
        prev_day = simulation_menu.addAction("Poprzedni dzień")
        prev_day.setShortcut("Ctrl+Left")
        prev_day.triggered.connect(lambda: self._step_day(-1))

        view_menu = self.menuBar().addMenu("Widok")
        autoscale_action = view_menu.addAction("Autoscale")
        autoscale_action.setShortcut("Ctrl+A")
        autoscale_action.triggered.connect(self._refit)
        follow_action = view_menu.addAction("Follow za świecą")
        follow_action.triggered.connect(
            lambda: self.control_bar.follow_button.setChecked(True)
        )

        help_menu = self.menuBar().addMenu("Pomoc")
        about_action = help_menu.addAction("O aplikacji")
        about_action.triggered.connect(self._show_about)

    def _connect_signals(self) -> None:
        bar = self.control_bar
        bar.open_requested.connect(self._choose_file)
        bar.generate_requested.connect(self.generate_default)
        bar.day_changed.connect(self._on_day_changed)
        bar.play_toggled.connect(self._on_play_toggled)
        bar.step_requested.connect(self._on_step)
        bar.progress_changed.connect(self._on_progress)
        bar.volume_changed.connect(self._on_volume_changed)
        bar.autoscale_toggled.connect(self._on_autoscale_toggled)
        bar.follow_toggled.connect(self.chart.set_autofollow)

    # ------------------------------------------------------------------
    # wczytywanie danych
    # ------------------------------------------------------------------
    def load_file(self, path: str | Path) -> bool:
        """Wczytuje plik HDF5 i buduje wspólną oś czasu dla wszystkich dni."""
        try:
            data = SimulationData(path)
            days = data.day_indices()
            if not days:
                raise ValueError("plik nie zawiera żadnego dnia")
            timeline = data.timeline(self.control_bar.candle_volume())
        except Exception as error:  # noqa: BLE001 - komunikat dla użytkownika
            QtWidgets.QMessageBox.critical(
                self, "Nie można otworzyć pliku", f"{path}\n\n{error}"
            )
            return False

        self.data = data
        self.timeline = timeline
        self.control_bar.set_days(days)
        self.file_label.setText(
            f"{Path(path)}  ·  seed {data.seed}  ·  świeca {timeline.spans[0].candle_target_volume}"
            if timeline.spans
            else str(Path(path))
        )
        self.chart.set_window(150)
        self.chart.set_series(timeline.candles, timeline.spans)
        self._fitted_day = None
        self._rewind()
        return True

    @property
    def total_trades(self) -> int:
        return self.timeline.trade_count if self.timeline else 0

    # ------------------------------------------------------------------
    # sterowanie pozycją
    # ------------------------------------------------------------------
    def _rewind(self) -> None:
        self.control_bar.set_playing(False)
        self.position = 0
        self.chart.rewind()
        self.control_bar.set_range(self.total_trades)
        self.control_bar.set_day(0)
        self._update_status()

    def _set_position(self, position: int) -> None:
        total = self.total_trades
        position = max(0, min(int(position), total))
        self.position = position
        self._syncing = True
        try:
            self._render()
            self.control_bar.set_progress(position, total)
            self._sync_day_selector()
        finally:
            self._syncing = False
        self._update_status()

    def _day_position(self) -> int:
        span = self._current_span()
        if span is None or not self.timeline:
            return 0
        try:
            return self.timeline.spans.index(span)
        except ValueError:
            return 0

    def _on_step(self) -> None:
        if not self.timeline:
            return
        self.control_bar.set_playing(False)
        self.control_bar.follow_button.setChecked(True)
        self._set_position(self.position + 1)

    def _on_tick(self) -> None:
        step = self.control_bar.trades_per_tick()
        if self.position >= self.total_trades:
            self.control_bar.set_playing(False)
            return
        self._set_position(self.position + step)

    def _on_play_toggled(self, playing: bool) -> None:
        if playing:
            if not self.timeline:
                self.control_bar.set_playing(False)
                return
            if self.position >= self.total_trades:
                self._rewind()
            self.control_bar.follow_button.setChecked(True)
            self._timer.start()
        else:
            self._timer.stop()

    def _on_progress(self, value: int) -> None:
        if not self.timeline:
            return
        self.control_bar.set_playing(False)
        self._set_position(value)

    def _on_day_changed(self, day_position: int) -> None:
        if not self.timeline or not self.timeline.spans:
            return
        if getattr(self, "_syncing", False):
            # zmiana spinnera sterowana programowo nie może przestawiać pozycji
            return
        self.control_bar.set_playing(False)
        spans = self.timeline.spans
        day_position = max(0, min(day_position, len(spans) - 1))
        self.control_bar.set_day(day_position)
        self._fitted_day = None
        self._set_position(self._day_jump_position(day_position))

    def _day_jump_position(self, day_position: int) -> int:
        """Pozycja pokazująca pierwszą świecę danego dnia."""
        if self.timeline is None:
            return 0
        if day_position <= 0 or not self.timeline.spans:
            return 0
        span = self.timeline.spans[day_position]
        for position in range(1, self.total_trades + 1):
            index, _closed = self.timeline.state_at(position)
            if index >= span.start_candle:
                return position
        return self.total_trades

    def _step_day(self, delta: int) -> None:
        if not self.timeline or not self.timeline.spans:
            return
        current = self._current_span()
        index = self.timeline.spans.index(current) if current else 0
        self._on_day_changed(index + delta)

    def _on_volume_changed(self, value: int) -> None:
        if self.data is None:
            return
        self.timeline = self.data.timeline(value, refresh=True)
        self.chart.set_series(self.timeline.candles, self.timeline.spans)
        self._fitted_day = None
        self._jump = -1
        self._rewind()

    def _on_autoscale_toggled(self, enabled: bool) -> None:
        self.chart.set_autoscale(enabled)
        self._fitted_day = None
        self._render()

    def _refit(self) -> None:
        self._fitted_day = None
        self.chart.set_autoscale(True)
        self.control_bar.autoscale_button.setChecked(True)
        self._render()

    # ------------------------------------------------------------------
    # rysowanie
    # ------------------------------------------------------------------
    def _render(self) -> None:
        timeline = self.timeline
        if timeline is None or timeline.candle_count == 0:
            self.chart.rewind()
            return
        if self.position <= 0:
            # start: nic nie jest jeszcze narysowane
            self.chart.rewind()
            return
        index, closed = timeline.state_at(self.position)
        last_price = timeline.trades[self.position - 1].price
        candle = (
            timeline.candles[index]
            if closed
            else timeline.partial_candle(index, self.position)
        )
        if candle is None:
            candle = timeline.candles[index]
        span = timeline.span_for_candle(index)
        day_index = span.day_index if span else None
        if self.control_bar.is_autoscale() and day_index != self._fitted_day:
            self.chart.refit_for_day(day_index)
            self._fitted_day = day_index
        self.chart.update_candles(index, closed, last_price, candle)

    def _current_span(self) -> DaySpan | None:
        if not self.timeline or self.timeline.candle_count == 0:
            return None
        index, _closed = self.timeline.state_at(self.position)
        return self.timeline.span_for_candle(index)

    def _sync_day_selector(self) -> None:
        span = self._current_span()
        if span is None or not self.timeline:
            return
        try:
            position = self.timeline.spans.index(span)
        except ValueError:
            return
        self.control_bar.set_day(position)

    # ------------------------------------------------------------------
    # akcje
    # ------------------------------------------------------------------
    def _choose_file(self) -> None:
        start = str(self.data.path.parent if self.data else Path.cwd())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Otwórz zapis symulacji", start, "HDF5 (*.h5 *.hdf5)"
        )
        if path:
            self.load_file(path)

    def generate_default(self) -> None:
        """Generuje nowy zapis symulacji; pyta o liczbę dni."""
        config = load_config()
        days, accepted = QtWidgets.QInputDialog.getInt(
            self,
            "Generowanie zapisu",
            "Liczba dni do wygenerowania:",
            config.simulation.days_to_generate,
            1,
            365,
            1,
        )
        if not accepted:
            return

        output = DEFAULT_OUTPUT
        output.parent.mkdir(parents=True, exist_ok=True)
        progress = QtWidgets.QProgressDialog(
            "Generowanie symulacji…", None, 0, days, self
        )
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        QtWidgets.QApplication.processEvents()
        try:
            result = run(config=config, days=days, output=output, verbose=False)
        except Exception as error:  # noqa: BLE001 - komunikat dla użytkownika
            progress.close()
            QtWidgets.QMessageBox.critical(self, "Błąd generowania", str(error))
            return
        progress.close()
        self.load_file(output)
        self.statusBar().showMessage(
            f"Zapisano {len(result['days'])} dzień/dni do {output}", 5000
        )

    def _show_about(self) -> None:
        QtWidgets.QMessageBox.information(
            self,
            "O aplikacji",
            "AMT x CAS — symulator rynku\n\n"
            "Każda świeca zbiera dokładnie tyle wolumenu, ile ustawiono\n"
            "w polu „Wolumen świecy” (nadmiar transakcji przechodzi do\n"
            "następnej świecy). Play/krok przesuwa po jednej transakcji,\n"
            "więc świeca w budowie rośnie na oczach, a domyka się po\n"
            "zebraniu pełnego wolumenu. Wykres startuje pusty i przewija\n"
            "się za bieżącą świecą (Follow).",
        )

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def _update_status(self) -> None:
        timeline = self.timeline
        if timeline is None or timeline.candle_count == 0 or self.position <= 0:
            if timeline is not None and timeline.candle_count:
                self.metrics.setText(
                    f"Start | świec {timeline.candle_count} | "
                    f"transakcji {timeline.trade_count} | seed "
                    f"{self.data.seed if self.data else '-'}"
                )
            else:
                self.metrics.setText("Brak danych")
            return
        index, closed = timeline.state_at(self.position)
        candle = (
            timeline.candles[index]
            if closed
            else timeline.partial_candle(index, self.position)
        )
        if candle is None:
            candle = timeline.candles[index]
        span = timeline.span_for_candle(index)
        state = "domknięta" if closed else "w budowie"
        day = f"Dzień {span.day_index}/{len(timeline.spans) - 1}" if span else "-"
        turn = self.timeline.trades[self.position - 1].timestamp
        target = span.candle_target_volume if span else 0
        seed = self.data.seed if self.data else "-"
        self.metrics.setText(
            f"{day} | świeca {index + 1}/{timeline.candle_count} ({state}) | "
            f"tura {turn:.2f}s | transakcja {self.position}/{timeline.trade_count} | "
            f"wolumen świecy {candle.volume}/{target} | "
            f"O {candle.open:.2f} H {candle.high:.2f} L {candle.low:.2f} "
            f"C {candle.close:.2f} | seed {seed}"
        )

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)
