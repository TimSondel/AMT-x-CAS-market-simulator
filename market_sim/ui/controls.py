"""Pasek sterowania: plik, dzień, wolumen świecy, odtwarzanie."""

from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

SPEED_FACTORS = {"1x": 1, "2x": 2, "4x": 4, "8x": 8}
BASE_TRADES_PER_TICK = 3


class ControlBar(QtWidgets.QWidget):
    """Play/pauza/krok, prędkość, dzień, wolumen świecy, autoscale, follow."""

    open_requested = QtCore.pyqtSignal()
    generate_requested = QtCore.pyqtSignal()
    day_changed = QtCore.pyqtSignal(int)
    play_toggled = QtCore.pyqtSignal(bool)
    step_requested = QtCore.pyqtSignal()
    progress_changed = QtCore.pyqtSignal(int)
    speed_changed = QtCore.pyqtSignal(int)
    volume_changed = QtCore.pyqtSignal(int)
    autoscale_toggled = QtCore.pyqtSignal(bool)
    follow_toggled = QtCore.pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._days = 0
        self._speed = 1
        self._label = "transakcja"

        self.open_button = QtWidgets.QPushButton("Otwórz plik…")
        self.generate_button = QtWidgets.QPushButton("Generuj zapis")
        self.play_button = QtWidgets.QPushButton("▶ Play")
        self.play_button.setCheckable(True)
        self.step_button = QtWidgets.QPushButton("⏭ Krok")

        self.volume_spin = QtWidgets.QSpinBox()
        self.volume_spin.setRange(10, 100000)
        self.volume_spin.setSingleStep(10)
        self.volume_spin.setValue(100)
        self.volume_spin.setToolTip("Wolumen na jedną świecę")
        self._volume_ready = False

        self.autoscale_button = QtWidgets.QPushButton("Autoscale")
        self.autoscale_button.setCheckable(True)
        self.autoscale_button.setChecked(True)
        self.autoscale_button.setToolTip("Dopasowanie osi ceny do widocznych świec")
        self._autoscale_ready = False

        self.follow_button = QtWidgets.QPushButton("Follow")
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(True)
        self.follow_button.setToolTip("Przewijanie wykresu za bieżącą świecą")
        self._follow_ready = False

        self.day_spin = QtWidgets.QSpinBox()
        self.day_spin.setRange(0, 0)
        self.day_spin.setPrefix("dzień ")
        self.day_spin.setEnabled(False)
        self.day_total = QtWidgets.QLabel("/ 0")

        self.speed_box = QtWidgets.QComboBox()
        self.speed_box.addItems(list(SPEED_FACTORS))
        self.speed_box.setCurrentText("2x")

        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setEnabled(False)
        self.progress = QtWidgets.QLabel("0 / 0")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        layout.addWidget(self.open_button)
        layout.addWidget(self.generate_button)
        layout.addSpacing(10)
        layout.addWidget(self.play_button)
        layout.addWidget(self.step_button)
        layout.addWidget(QtWidgets.QLabel("Prędkość:"))
        layout.addWidget(self.speed_box)
        layout.addSpacing(10)
        layout.addWidget(QtWidgets.QLabel("Dzień:"))
        layout.addWidget(self.day_spin)
        layout.addWidget(self.day_total)
        layout.addSpacing(10)
        layout.addWidget(QtWidgets.QLabel("Wolumen świecy:"))
        layout.addWidget(self.volume_spin)
        layout.addWidget(self.autoscale_button)
        layout.addWidget(self.follow_button)
        layout.addSpacing(10)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.progress)

        self.open_button.clicked.connect(self.open_requested.emit)
        self.generate_button.clicked.connect(self.generate_requested.emit)
        self.play_button.toggled.connect(self._on_play_toggled)
        self.step_button.clicked.connect(self.step_requested.emit)
        self.day_spin.valueChanged.connect(self._on_day_changed)
        self.slider.valueChanged.connect(self._on_slider_changed)
        self.speed_box.currentTextChanged.connect(self._on_speed_changed)
        self.volume_spin.valueChanged.connect(self.volume_changed.emit)
        self.autoscale_button.toggled.connect(self._on_autoscale_toggled)
        self.follow_button.toggled.connect(self._on_follow_toggled)
        self._volume_ready = True
        self._autoscale_ready = True
        self._follow_ready = True

    # ------------------------------------------------------------------
    # stan widoku
    # ------------------------------------------------------------------
    def set_days(self, day_indices: list[int]) -> None:
        self._days = len(day_indices)
        self.day_spin.blockSignals(True)
        self.day_spin.setRange(0, max(0, self._days - 1))
        self.day_spin.setValue(0)
        self.day_spin.setEnabled(self._days > 0)
        self.day_spin.blockSignals(False)
        self.day_total.setText(f"/ {max(0, self._days - 1)}")

    def set_day(self, day_position: int) -> None:
        self.day_spin.blockSignals(True)
        self.day_spin.setValue(day_position)
        self.day_spin.blockSignals(False)

    def set_range(self, total: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, total))
        self.slider.setValue(0)
        self.slider.setEnabled(total > 0)
        self.slider.blockSignals(False)
        self.progress.setText(f"0 / {total}")

    def set_progress(self, current: int, total: int) -> None:
        # blokada sygnałów: pasek ma tylko pokazywać stan, nie sterować
        self.slider.blockSignals(True)
        self.slider.setValue(current)
        self.slider.blockSignals(False)
        self.progress.setText(f"{current} / {total}")
        if current >= total:
            self.set_playing(False)

    def set_playing(self, playing: bool) -> None:
        """Ustawia stan przycisku play i emituje sygnał przy zmianie stanu."""
        if self.play_button.isChecked() == playing:
            return
        self.play_button.blockSignals(True)
        self.play_button.setChecked(playing)
        self.play_button.setText("⏸ Pauza" if playing else "▶ Play")
        self.play_button.blockSignals(False)
        self.play_toggled.emit(playing)

    def is_playing(self) -> bool:
        return self.play_button.isChecked()

    def is_autoscale(self) -> bool:
        return self.autoscale_button.isChecked()

    def is_follow(self) -> bool:
        return self.follow_button.isChecked()

    def candle_volume(self) -> int:
        return self.volume_spin.value()

    def trades_per_tick(self) -> int:
        return max(1, self._speed * BASE_TRADES_PER_TICK)

    def interval_ms(self) -> int:
        return 100

    # ------------------------------------------------------------------
    # sygnały
    # ------------------------------------------------------------------
    def _on_play_toggled(self, playing: bool) -> None:
        self.play_button.setText("⏸ Pauza" if playing else "▶ Play")
        self.play_toggled.emit(playing)

    def _on_day_changed(self, value: int) -> None:
        self.day_changed.emit(value)

    def _on_slider_changed(self, value: int) -> None:
        self.progress_changed.emit(value)

    def _on_speed_changed(self, text: str) -> None:
        self._speed = SPEED_FACTORS.get(text, 1)
        self.speed_changed.emit(self._speed)

    def _on_autoscale_toggled(self, enabled: bool) -> None:
        self.autoscale_button.setText("Autoscale ✓" if enabled else "Autoscale")
        if self._autoscale_ready:
            self.autoscale_toggled.emit(enabled)

    def _on_follow_toggled(self, enabled: bool) -> None:
        self.follow_button.setText("Follow ✓" if enabled else "Follow")
        if self._follow_ready:
            self.follow_toggled.emit(enabled)
