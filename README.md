# AMT x CAS market simulator

Symulator rynku: orderbook + procesy percepcji wartości → strumień transakcji →
świece wolumenowe i volume profile → zapis HDF5.

## Uruchomienie

Aplikacja z wykresem świec wolumenowych:

```bash
python main.py                 # otwiera data/simulation.h5, jeśli istnieje
python main.py sciezka/do/zapis.h5
```

Obsługa okna:

- `▶ Play` / `⏭ Krok` / suwak — przesuwają odtwarzanie o **transakcje**;
  świeca w budowie rośnie przy każdej transakcji i domyka się, gdy zbierze
  pełny wolumen (`Wolumen świecy`, domyślnie 100).
- `Wolumen świecy` — docelowy wolumen jednej świecy; zmiana przelicza oś czasu.
- `Autoscale` — dopasowanie osi ceny do zakresu dnia; `Follow` — przewijanie
  wykresu za bieżącą świecą (wyłączenie pozwala oglądać całość i zoomować).
- `Dzień` — skok do pierwszego dnia; na wykresie granice dni są zaznaczone
  pionową linią i etykietą „Dzień N”.
- Wykres startuje **pusty** — świece pojawiają się wraz z odtwarzaniem.
- `Plik → Generuj nowy zapis…` pyta o liczbę dni i zapisuje do `data/simulation.h5`.
- `Plik → Otwórz…` (Ctrl+O) wczytuje istniejący zapis.

Symulacja z linii poleceń (bez UI):

```bash
python -m market_sim.runner --days 1 --seed 7 --output simulation.h5
```

Argumenty: `--config`, `--days`, `--seed`, `--output`, `--quiet`.

## Testy

```bash
python -m pytest tests -q
```

Testy UI działają bez ekranu (`QT_QPA_PLATFORM=offscreen`) — nie wymagają pulpitu.

## Struktura

```
main.py                   # punkt wejścia aplikacji UI
market_sim/
├── config.py             # ConfigLoader (YAML + pydantic)
├── simulation.py         # Simulation: step, run_day, EOD, ledger pozycji
├── runner.py             # CLI: generacja dni + zapis HDF5
├── main.py               # QApplication + MainWindow
├── core/
│   ├── order.py          # Order, Trade, Side
│   ├── orderbook.py      # OrderBook: add_limit, execute_market, refill
│   ├── processes.py      # OU, jump-diffusion, reżimy Markowa, log-OU
│   ├── perception.py     # p_long, rozkłady rozmiaru i offsetu
│   └── volume.py         # krzywa wolumenu intraday (U-shape)
├── storage/
│   ├── aggregate.py      # CandleBuilder (świece wolumenowe), volume profile
│   ├── writer.py         # HDF5Writer
│   └── reader.py         # HDF5Reader
└── ui/
    ├── main_window.py    # MainWindow: menu, pasek sterowania, status, odtwarzanie
    ├── controls.py       # ControlBar: play/krok/dzień/wolumen świecy/autoscale/follow
    ├── candle_chart.py   # CandleChartWidget + CandlestickItem (granice dni, autofollow)
    ├── timeline.py       # Timeline: świece wolumenowe wszystkich dni + mapowanie transakcji
    └── data_source.py    # SimulationData: HDF5 → transakcje i oś czasu
tests/
├── test_orderbook.py     # orderbook
├── test_simulation.py    # procesy, dzień, EOD, zapis/odczyt
└── test_ui.py            # oś czasu, wykres, pasek sterowania, okno główne
```

## Format zapisu (`simulation.h5`)

```
meta                     attrs: seed, version, created_at; dataset: config_yaml
days/day_0000/
  trades                 [timestamp, price, size, aggressor_side, day_index]
  candles                [open, high, low, close, volume, open_time, close_time, day_index]
  profile/prices         biny cenowe (tick_size)
  profile/volumes        wolumen na bin; attrs: poc, vah, val
  meta                   attrs: open, close, high, low, total_volume, num_trades, ...
index                    [day_index, open, close]
```

Świece są wolumenowe: każda zbiera dokładnie `volume.candle_target_volume`
wolumenu, a nadmiar pojedynczej transakcji przechodzi do kolejnej świecy
(overflow). UI może przeliczyć oś czasu na inny wolumen świecy bez zmiany pliku.
