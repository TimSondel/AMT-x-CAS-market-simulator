"""Wykres świec wolumenowych: okno widoku, granice dni, autoscale."""

from __future__ import annotations

import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

from market_sim.storage.aggregate import Candle

UP_COLOR = "#26a69a"
DOWN_COLOR = "#ef5350"
BACKGROUND = "#ffffff"
GRID_ALPHA = 0.25
LINE_COLOR = "#2962ff"
SEPARATOR_COLOR = "#b0bec5"


class CandlestickItem(pg.GraphicsObject):
    """Rysuje świece: knot (high-low) + korpus (open-close)."""

    def __init__(self, candles: list[Candle] | None = None):
        super().__init__()
        self.candles: list[Candle] = []
        self.first_index = 0
        self.width = 0.7
        self._picture = QtGui.QPicture()
        self._bounds = QtCore.QRectF()
        if candles:
            self.set_candles(candles)

    def set_candles(self, candles: list[Candle], first_index: int = 0) -> None:
        """Ustawia świece; obraz odtwarzany tylko, gdy zmieni się zawartość."""
        candles = list(candles)
        if candles == self.candles and first_index == self.first_index:
            return
        self.candles = candles
        self.first_index = first_index
        self._regenerate()
        self.informViewBoundsChanged()
        self.update()

    def set_candle(self, candle: Candle | None, index: int = 0) -> None:
        """Ustawia pojedynczą świecę (np. w budowie); None czyści."""
        if candle is None:
            self.set_candles([], 0)
        else:
            self.set_candles([candle], index)

    def _regenerate(self) -> None:
        painter = QtGui.QPainter(self._picture)
        half = self.width / 2.0
        if self.candles:
            lows = min(candle.low for candle in self.candles)
            highs = max(candle.high for candle in self.candles)
            self._bounds = QtCore.QRectF(
                self.first_index - half,
                lows,
                len(self.candles) - 1 + self.width,
                max(highs - lows, 1e-9),
            )
        else:
            self._bounds = QtCore.QRectF()
        for offset, candle in enumerate(self.candles):
            index = self.first_index + offset
            color = QtGui.QColor(
                UP_COLOR if candle.close >= candle.open else DOWN_COLOR
            )
            painter.setPen(pg.mkPen(color, width=1))
            painter.drawLine(
                QtCore.QPointF(index, candle.low),
                QtCore.QPointF(index, candle.high),
            )
            body_low = min(candle.open, candle.close)
            body_high = max(candle.open, candle.close)
            height = max(body_high - body_low, 1e-9)
            painter.setBrush(pg.mkBrush(color))
            painter.drawRect(
                QtCore.QRectF(index - half, body_low, self.width, height)
            )
        painter.end()

    def paint(self, painter, option, widget=None) -> None:
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self) -> QtCore.QRectF:
        return self._bounds


class CandleChartWidget(QtWidgets.QWidget):
    """Wykres świec z panelem wolumenu, linią ceny i granicami dni."""

    def __init__(self, parent=None):
        super().__init__(parent)
        pg.setConfigOptions(antialias=False, background=BACKGROUND)
        pg.setConfigOption("foreground", "#444444")

        self.price_plot = pg.PlotWidget()
        self.volume_plot = pg.PlotWidget()
        self.price_plot.showGrid(x=True, y=True, alpha=GRID_ALPHA)
        self.volume_plot.showGrid(x=True, y=True, alpha=GRID_ALPHA)
        self.price_plot.setLabel("left", "Cena")
        self.volume_plot.setLabel("left", "Wolumen / świeca")
        self.volume_plot.setLabel("bottom", "Nr świecy")
        self.price_plot.getAxis("bottom").setStyle(showValues=False)
        self.price_plot.setMouseEnabled(x=True, y=True)
        self.volume_plot.setMouseEnabled(x=True, y=False)
        self.volume_plot.setXLink(self.price_plot)

        self.candle_item = CandlestickItem()
        self.price_plot.addItem(self.candle_item)
        self.forming_item = CandlestickItem()
        self.price_plot.addItem(self.forming_item)
        self.volume_item = pg.BarGraphItem(x=[], height=[], width=0.7, brush=UP_COLOR)
        self.volume_plot.addItem(self.volume_item)

        self.separator_pen = pg.mkPen(SEPARATOR_COLOR, width=1)
        self.separators: list[tuple[pg.InfiniteLine, pg.TextItem]] = []

        self.marker_line = pg.InfiniteLine(
            angle=0,
            movable=False,
            pen=pg.mkPen(LINE_COLOR, width=1, style=QtCore.Qt.PenStyle.DashLine),
        )
        self.marker_text = pg.TextItem(color=LINE_COLOR, anchor=(1, 0.5))
        self.price_plot.addItem(self.marker_line, ignoreBounds=True)
        self.price_plot.addItem(self.marker_text, ignoreBounds=True)
        self.marker_line.hide()
        self.marker_text.hide()

        self.status_text = pg.TextItem(color="#222222", anchor=(0, 0))
        self.price_plot.addItem(self.status_text, ignoreBounds=True)
        self.status_text.setPos(0, 0)

        self.message = QtWidgets.QLabel("Brak danych")
        self.message.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.message.setStyleSheet("color: #777777; font-size: 13px;")

        layout = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        layout.addWidget(self.price_plot)
        layout.addWidget(self.volume_plot)
        layout.setStretchFactor(0, 3)
        layout.setStretchFactor(1, 1)
        layout.setSizes([480, 180])

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.message)
        outer.addWidget(layout)

        self.autoscale = True
        self.autofollow = True
        self.window = 150
        self._candles: list[Candle] = []
        self._spans: list = []
        self._boundaries: list[tuple[int, int]] = []
        self._current = -1
        self._closed = True
        self._last_y: float | None = None
        self._partial: Candle | None = None
        self._painted_range: tuple[int, int] | None = None
        self._x_set = False

    # ------------------------------------------------------------------
    # publiczne API
    # ------------------------------------------------------------------
    def set_series(self, candles: list[Candle], spans: list | None = None) -> None:
        """Ustawia pełną serię świec (wszystkie dni) i rysuje początkowe okno."""
        self._candles = list(candles)
        self._spans = list(spans or [])
        self._boundaries = [
            (span.start_candle, span.day_index)
            for span in self._spans
            if span.start_candle > 0
        ]
        self._rebuild_separators()
        self.rewind()

    def rewind(self) -> None:
        """Stan początkowy: nic nie jest jeszcze narysowane."""
        self._current = -1
        self._closed = True
        self._last_y = None
        self._partial = None
        self._painted_range = None
        self._x_set = False
        self._draw()

    def update_candles(
        self,
        current_index: int,
        closed: bool,
        last_price: float | None,
        partial: Candle | None = None,
    ) -> None:
        """Rysuje okno świec kończące się na `current_index`.

        `partial` to świeca w budowie (tylko transakcje do bieżącej pozycji),
        dzięki czemu widać jej formowanie się z każdym krokiem.
        """
        self._current = current_index
        self._closed = closed
        self._last_y = last_price
        self._partial = partial
        self._draw()

    def set_autoscale(self, enabled: bool) -> None:
        self.autoscale = bool(enabled)
        self._draw()

    def set_autofollow(self, enabled: bool) -> None:
        self.autofollow = bool(enabled)
        self._x_set = False
        self.refit_view()

    def refit_view(self) -> None:
        """Wymusza ponowne ustawienie zakresów osi."""
        self._x_set = False
        self._draw()

    def set_window(self, candles: int) -> None:
        self.window = max(10, int(candles))
        self._x_set = False

    def refit_for_day(self, day_index: int | None, margin: float = 0.15) -> None:
        """Dopasowuje oś ceny do zakresu wybranego dnia (stabilny efekt)."""
        span = self._span_for_day(day_index)
        if span is None:
            return
        visible = self._candles[span.start_candle : span.end_candle + 1]
        if not visible:
            return
        low = min(candle.low for candle in visible)
        high = max(candle.high for candle in visible)
        pad = max(high - low, self._tick_floor()) * margin
        self.price_plot.setYRange(low - pad, high + pad, padding=0)

    def _tick_floor(self) -> float:
        prices = [
            candle.high - candle.low for candle in self._candles[:200]
        ]
        return max(min([value for value in prices if value > 0] or [0.25]), 1e-6)

    def _span_for_day(self, day_index: int | None):
        for span in self._spans:
            if span.day_index == day_index:
                return span
        return None

    @property
    def total_count(self) -> int:
        return len(self._candles)

    @property
    def current_index(self) -> int:
        return self._current

    @property
    def drawn_candles(self) -> list[Candle]:
        """Wszystkie narysowane świece (domknięte + w budowie)."""
        return list(self.candle_item.candles) + list(self.forming_item.candles)

    # ------------------------------------------------------------------
    # rysowanie
    # ------------------------------------------------------------------
    def _visible(self) -> tuple[int, int, list[Candle]]:
        """Zakres świec do narysowania.

        Ostatnia pozycja to świeca w budowie: częściowa bieżąca (zastępuje
        domkniętą wersję), a po jej domknięciu nadchodząca, jeszcze niepełna.
        """
        if self._current < 0 or not self._candles:
            return 0, -1, []
        current = min(self._current, len(self._candles) - 1)
        if self._closed:
            last = current
            forming = None
            window = self._candles[: last + 1]
        elif self._partial is not None:
            # bieżąca świeca jest w budowie — rysujemy jej częściową wersję
            last = current
            forming = self._partial
            window = self._candles[:current] + [forming]
        elif current + 1 < len(self._candles):
            # bieżąca świeca właśnie się domknęła — pokazujemy nadchodzącą
            last = current + 1
            forming = self._candles[last]
            window = self._candles[:last] + [forming]
        else:
            last = current
            forming = self._candles[last]
            window = list(self._candles)
        start = max(0, last - self.window + 1) if self.autofollow else 0
        return start, last, window[start:]

    def _draw(self) -> None:
        start, end, visible = self._visible()
        total = len(self._candles)
        # świeca w budowie to ostatnia pozycja okna (jeśli wyprzedza bieżącą)
        forming = visible[-1] if end > self._current else None
        self.message.setVisible(not visible)
        if not visible:
            self.candle_item.set_candles([], 0)
            self.forming_item.set_candle(None)
            self.volume_item.setOpts(x=[], height=[])
            self.marker_line.hide()
            self.marker_text.hide()
            self.status_text.setText(
                f"świec: 0 / {total}" if total else "brak świec"
            )
            self._painted_range = None
            return

        # świece domknięte rysuje candle_item (przerysowanie tylko przy zmianie
        # zakresu), świecę w budowie osobny item — tanie odświeżanie co krok
        if forming is not None:
            closed_part = visible[:-1]
            self.candle_item.set_candles(closed_part, start)
            self.forming_item.set_candle(forming, end)
        else:
            self.candle_item.set_candles(visible, start)
            self.forming_item.set_candle(None)
        self.volume_item.setOpts(
            x=list(range(start, end + 1)),
            height=[candle.volume for candle in visible],
            width=0.7,
            brushes=[
                pg.mkBrush(UP_COLOR if candle.close >= candle.open else DOWN_COLOR)
                for candle in visible
            ],
        )
        self._painted_range = (start, end)

        if self._last_y is not None:
            self.marker_line.setPos(self._last_y)
            self.marker_line.show()
            self.marker_text.setPos(end, self._last_y)
            self.marker_text.setText(f"{self._last_y:.2f}")
            self.marker_text.show()
        else:
            self.marker_line.hide()
            self.marker_text.hide()

        self.status_text.setText(self._describe(visible, end, total))
        if self.autoscale:
            self._fit_axes(visible, start, end)
        self._update_separators(start, end)

    def _fit_axes(self, visible: list[Candle], start: int, end: int) -> None:
        """Stabilny zakres: oś Y ustawia refit_for_day, tu tylko X i wolumen."""
        top = max([candle.volume for candle in visible] + [1])
        self.volume_plot.setYRange(0, top * 1.15, padding=0)
        if self.autofollow:
            left = max(-1, end - self.window + 1) if len(self._candles) > self.window else -1
            right = max(left + 3, end + 2)
            self.price_plot.setXRange(left, right, padding=0)
            self.volume_plot.setXRange(left, right, padding=0)
        elif not self._x_set:
            self.price_plot.setXRange(-1, max(3, len(self._candles) + 1), padding=0)
            self.volume_plot.setXRange(-1, max(3, len(self._candles) + 1), padding=0)
        self._x_set = True
        high = self.price_plot.viewRange()[1][1]
        self.status_text.setPos(start, high)

    def _rebuild_separators(self) -> None:
        for line, label in self.separators:
            self.price_plot.removeItem(line)
            self.price_plot.removeItem(label)
        self.separators = []
        for index, day_index in self._boundaries:
            line = pg.InfiniteLine(
                pos=index - 0.5, angle=90, movable=False, pen=self.separator_pen
            )
            label = pg.TextItem(
                f"Dzień {day_index}", color="#546e7a", anchor=(0, 0)
            )
            self.price_plot.addItem(line, ignoreBounds=True)
            self.price_plot.addItem(label, ignoreBounds=True)
            self.separators.append((line, label))

    def _update_separators(self, start: int, end: int) -> None:
        high = self.price_plot.viewRange()[1][1]
        for (line, label), (index, _day) in zip(self.separators, self._boundaries):
            label.setPos(index - 0.4, high)
            label.setVisible(start <= index <= end)

    def _describe(self, candles: list[Candle], end: int, total: int) -> str:
        if not candles:
            return f"świec: 0 / {total}" if total else "brak świec"
        first, last = candles[0], candles[-1]
        high = max(candle.high for candle in candles)
        low = min(candle.low for candle in candles)
        volume = sum(candle.volume for candle in candles)
        span = self._span_for(end)
        day = f"Dzień {span.day_index}  " if span is not None else ""
        return (
            f"{day}świec: {len(candles)}/{total}   "
            f"O: {first.open:.2f}  H: {high:.2f}  L: {low:.2f}  C: {last.close:.2f}   "
            f"wolumen: {volume:,}".replace(",", " ")
        )

    def _span_for(self, candle_index: int):
        for span in self._spans:
            if span.start_candle <= candle_index <= span.end_candle:
                return span
        return None
