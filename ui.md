2.2. Komponenty UI
Komponent	Klasa	Odpowiedzialność
Okno główne	MainWindow	Layout, menu, splittter, spina sygnały
Pasek sterowania	ControlBar	Play/pause/krok/prędkość/wybór dnia, seed, save/load
Wykres świec	CandleChartWidget	Render świec wolumenowych, zoom/pan, przewijanie dni
Volume profile	VolumeProfileWidget	Histogram po cenie dla wybranego dnia, POC/VAH/VAL
Status bar	StatusBar	Metryki na żywo (mid, spread, wolumen)
Pasek postępu	(wbudowany)	Postęp generowania dnia
2.3. Model danych
text
Order
  id, side, price, size, remaining, timestamp, tag, agent_ref

Trade
  timestamp, price, size, aggressor_side, day_index

Level
  price, total_size, orders: List[Order]

OrderBook
  bids: SortedDict[price → Level]
  asks: SortedDict[price → Level]
  best_bid, best_ask
  history: List[Trade]

DayMeta
  day_index, open, close, high, low, total_volume, num_trades,
  open_positions_after, regime_path, seed

Candle (wolumenowa)
  open, high, low, close, volume, open_time, close_time, day_index

VolumeProfile
  day_index, prices: List[float], volumes: List[int],
  poc, vah, val
2.4. Format zapisu (HDF5)
text
simulation.h5
├── meta
│   ├── config_yaml         (string)
│   ├── seed                (int)
│   ├── version             (string)
│   └── created_at          (timestamp)
├── days
│   ├── day_0000/
│   │   ├── trades          (structured array)
│   │   ├── candles         (structured array)
│   │   ├── profile         (dataset)
│   │   └── meta            (attrs: open, close, high, low, vol)
│   ├── day_0001/
│   └── ...
└── index                   (lista dni + ich zakresy czasowe)