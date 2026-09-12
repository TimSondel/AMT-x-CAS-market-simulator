Plan symulatora rynkowego — rekomendacje
Poniżej propozycja kompleksowa: rozkłady/funkcje dla każdego modułu + architektura projektu + uwagi UI.

1. Percepcja wartości (fair value center)
Rekomendacja: proces OU (Ornstein–Uhlenbeck) + jump-diffusion (Merton) + regime switching (Markov)

text
dX_t = θ(μ − X_t)·dt + σ(r_t)·dW_t + J·dN_t
OU daje mean-reverting „fair value" (środek wraca do średniej)

Skoki Poissona (dN_t) — żeby czasem „rzadko ale mocno" się przesuwał

Regime switching (2–3 stany: quiet / normal / volatile) — rozwiązuje dokładnie twój problem „czasem rzadko, czasem częściej"

Parametry:

θ ~ 0.01–0.1 (tempo powrotu)

σ_quiet ≪ σ_normal ≪ σ_volatile

λ_jump (intensywność) i μ_jump, σ_jump (rozmiar skoku)

Macierz przejść między reżimami P (np. przebywanie w quiet ~ 200 kroków, w volatile ~ 30)

Alternatywa: fBm (fractional Brownian Motion) z H≈0.6–0.7 jeśli chcesz long memory.

2. Kształt rozkładu percepcji (rozkład wokół środka)
Rekomendacja: Student-t (df ≈ 3–5) lub Laplace

Normalny jest zbyt „grzeczny" — nie generuje agresywnych zleceń

Student-t daje grube ogony → sporadyczne dalekie zlecenia (realistyczne)

Laplace daje ostry szczyt + wykładnicze ogony (prostszy, szybszy)

python
p_long = sigmoid( certainty * (center − mid_price) / spread )
albo wersja CDF-owa:

python
p_long = 0.5 * (1 + erf((center − mid_price) / (spread * sqrt(2))))
3. Spread (szerokość percepcji)
Rekomendacja: log-normalny OU (zawsze dodatni, prawoskośny)

text
log(spread_t) ~ OU(μ_s, θ_s, σ_s)
Zmniejszony spread → węższe rotacje, szybsze price discovery

Zwiększony → szersza rotacja, mniej reakcji na odchylenia

Zakres wartości: np. 2–20 ticków

4. Pewność (mnożnik)
Rekomendacja: OU w przestrzeni log + transformacja sigmoidalna do [0.3, 2.5]

text
log(c_t) ~ OU  →  certainty = clip(exp(c_t), 0.3, 2.5)
Wysoka pewność → szybsze trendy, wyższe p_long przy danym odchyleniu

Niska → częste rotacje, „rozmyta" percepcja

Można dodać deterministyczny komponent sezonowy (np. sin) dla realizmu

5. Widoczność (limit vs market)
Rekomendacja: Beta(α, β) w [0,1] lub logit-OU

python
v = beta_sample(α, β)   # 0 = same limity, 1 = same markety
w_limit  = 1 − v
w_market = v
Zastosowanie:

w_market skaluje prawdopodobieństwo zleceń market w kierunku percepcji

w_limit skaluje wielkości uzupełnień limitów: po stronie „zgodnej" z percepcją większe, po przeciwnej mniejsze

Dobrym pomysłem jest sprzężenie widoczności z pewnością: „pewność rośnie → widoczność marketowa rośnie".

6. Wolumen intraday (U-shape)
Rekomendacja: mieszanina dwóch Gaussów + baseline

python
def volume_curve(t, T):  # t∈[0,1]
    open_peak  = A1 * exp(-((t - 0.03)**2) / (2*σ1**2))
    close_peak = A2 * exp(-((t - 0.97)**2) / (2*σ2**2))
    midday     = A3
    return open_peak + close_peak + midday
Alternatywa: Beta(a, b) z a,b < 1 — daje dokładnie kształt U.

Dla overnight: osobny, niski baseline (np. 0.1× dziennego).

7. Rozkład wielkości zleceń
Rekomendacja: log-normalny z ogonem Pareto

python
size = lognormal(μ=0, σ=1)              # 95% zleceń
if random() < 0.05:
    size *= pareto(α=1.5) + 1          # duże „block trades"
Alternatywa prosta: Pareto(α≈2) z minimum 1.

8. Czas między zleceniami (opcjonalnie)
Poisson (wykładniczy) — najprostsze

Hawkes (self-exciting) — klastrowanie wolumenu, realistyczniejsze

W praktyce dla kroków symulacji: w każdym kroku dokładnie 1 market order i deterministyczna liczba refilli, zgodnie z twoim założeniem

9. Koniec dnia — czyszczenie orderbooka
Prosty schemat:

Policz otwarte pozycje (na podstawie historii trade'ów po stronie market orderów, które nie zostały zamknięte)

Wygeneruj N „dużych" limitów po obu stronach (np. 500 szt.) na kilku tickach od mid

Zdeterminowanym % (np. 70%) zamknij pozycje — część traderów wychodzi, część trzyma overnight

Wyczyść orderbook do 20 ticków na stronę

Parametry w configu: eod_close_pct, eod_limit_size, eod_spread_ticks.

10. Architektura projektu
text
market_sim/
├── config/
│   ├── default.yaml            # wszystkie parametry domyślne
│   └── profiles/               # presety (calm, volatile, trending)
├── core/
│   ├── order.py                # Order, Trade
│   ├── orderbook.py            # OrderBook (limit + market, refill)
│   ├── processes.py            # OU, jump, regime, OU-lognormal
│   ├── perception.py           # value perception, spread, certainty, visibility
│   ├── volume.py               # intraday curve
│   ├── simulation.py           # pętla kroków, dzień, EOD
│   └── day_manager.py          # lifecycle dnia
├── storage/
│   ├── writer.py               # HDF5 / Parquet
│   ├── reader.py
│   └── schema.py               # wersjonowanie formatu
├── ui/
│   ├── main_window.py          # QMainWindow + QSplitter
│   ├── candle_chart.py         # pyqtgraph, świece wolumenowe
│   ├── volume_profile.py       # pyqtgraph, profil po cenie
│   ├── controls.py             # play/pause/krok/prędkość, wybór dnia
│   └── theme.py
├── main.py
└── requirements.txt
Kluczowe decyzje techniczne
UI: PyQtGraph, nie matplotlib. Świece rysuj przez custom GraphicsObject (jest gotowy przykład w pyqtgraph.examples). Zoom/pan wbudowane.

Świece wolumenowe (nie czasowe): akumuluj kontrakty do target_volume, gdy przekroczy próg → zamknij świecę, nadmiar przenieś do następnej (overflow). Trzymaj O/H/L/C z cen transakcji.

Volume profile: dla każdego dnia histogram po cenie (np.histogram z binami = tick size), rysuj jako poziomy słupek obok wykresu świec. Jeden profil = jeden dzień, przełączany razem z dniem na wykresie.

Zapis symulacji: HDF5 z datasetami trades/{day}/..., candles/{day}/..., profiles/{day}/..., plus meta.json z configiem i RNG seed. Odtwarzanie = wczytanie + ewentualne dopisanie kolejnych dni z zapisanym seedem.

Config: YAML + pydantic do walidacji. Wszystkie parametry (tick size, krok = 0.25 s, liczba tur w dniu, spread range, OU params, etc.).

11. RNG i powtarzalność
Jeden globalny np.random.Generator(seed=...) albo osobne generatory per moduł (percepcja, wolumen, zlecenia) — osobne są lepsze, bo zmiana jednego modułu nie psuje innych.

Seed zapisywany w metadanych symulacji → pełna reprodukowalność.

12. Kolejność implementacji (sugestia)
OrderBook + Order + podstawowy refill (bez percepcji)

Simulation z losowymi market orderami (kierunek 50/50) — sanity check

Procesy: OU, jump, regime — moduł processes.py

perception.py — sprzężenie percepcji z prawdopodobieństwem kierunku

Wolumen intraday + spread + certainty + visibility

EOD clearing

Storage (HDF5) + config

UI: najpierw sam wykres świec, potem volume profile, potem kontrolki

Zapis/odtwarzanie z UI

13. Kilka praktycznych uwag
Tick size 0.25 i krok 0.25 s — przy 20 tickach na stronę i typowym spreadzie 2–4 ticki to dobre proporcje; sprawdź empirycznie, że płynność nie znika.

Nie mieszaj jednostek: trzymaj wszystko w tickach wewnętrznie, konwertuj na cenę tylko w UI/storage.

Overflow świecy: jeśli pojedyncza transakcja przekracza target_volume, rozbij ją na kilka świec proporcjonalnie — inaczej powstają dziury.

Wydajność: pyqtgraph + numpy wystarczą dla milionów ticków. Unikaj per-tick Pythona w UI — renderuj z buforów.

Testy: napisz test „płynności" — po N krokach spread wrócił do zakresu, nie ma pustych poziomów w 20 tickach.