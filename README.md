# Symulator rynku – Stały bufor płynności (OrderBook 100 poziomów) + Prosta adaptacja FV

Symulator dyskretny (tury) napisany w Pythonie (biblioteka standardowa; matplotlib
jest opcjonalny i służy wyłącznie do wykresów). Implementuje wprost blueprint
„CAS + AMT": rynek jest **czysto emergentny** – nie ma narzuconego konsensusu ani
siły przyciągającej do średniej. Cena powstaje wyłącznie z interakcji zleceń
**Market** (składanych przez agentów) z **pasywną płynnością** (OrderBook).

Jedynym nośnikiem adaptacji jest zmiana subiektywnej **wartości godziwej (FV)**
agentów, która następuje **dopiero po zamknięciu transakcji** – w zależności od
tego, czy agent zarobił, czy stracił. Eliminuje to sztuczną średnią powrotną
i pozwala na naturalne trendy.

## Uruchomienie

```bash
# podstawowe uruchomienie (2000 tur, 300 agentów, seed=42)
python market_simulator.py

# dłuższa symulacja z wykresem interaktywnym
python market_simulator.py --turns 5000 --plot

# zapis historii do CSV + wykres do PNG (tryb bez GUI)
python market_simulator.py --turns 3000 --csv historia.csv --plot-save wykres.png

# zmiana parametrów (seed, liczba agentów, cena startowa, tick, bufor)
python market_simulator.py --seed 7 --agents 500 --price 100 --tick 0.05 --levels 100
```

Główne opcje CLI: `--turns --seed --price --tick --agents --levels --fv-spread
--threshold --sl --tp --base-passive --csv --plot --plot-save --quiet`.

## Model – mapa do blueprintu

### 2. Agent (jeden ujednolicony typ)

* **Inicjalizacja FV**: `FV ~ U(start·(1−5%), start·(1+5%))` – zróżnicowane percepcje.
* **Wielkość pozycji**: mieszanka 3 rozkładów normalnych
  (mały ~ N(5,1.5), średni ~ N(50,12), duży ~ N(400,120), wagi 0.6/0.3/0.1).
* **Wejście**: `dist = (cena − FV) / FV`; jeśli `|dist| > 0.5%`, agent składa
  zlecenie **Market** w kierunku powrotu do FV (kupuje tanio, sprzedaje drogo –
  kontrarianin).
* **Zarządzanie**: przy otwarciu losowane `SL = cena·(1∓0.8%)` i `TP = cena·(1±1.2%)`
  (z losowym szumem). **TP** trafia do książki jako zlecenie **Limit** oczekujące;
  **SL** jest sprawdzany co turę i realizowany jako **Market** przy przebiciu.

### 3. OrderBook (centralny mechanizm płynności)

* **Inicjalizacja**: 100 poziomów Ask powyżej i 100 Bid poniżej ceny startowej.
* **Wypełnienie**: losowy wolumen z wykładniczym spadkiem od ceny (przy cenie
  dużo, daleko mało) – początkowa „grubość" rynku.
* **Replenishment**: po każdej turze dopasowania poziomy częściowo/całkowicie
  wykupione są uzupełniane nowym losowym wolumenem; okno 100 poziomów jest
  przesuwane, aby zawsze utrzymać bufor 100 po każdej stronie (stare poziomy
  usuwane, nowe na krawędzi dodawane).
* **Realizacja**: Market → natychmiast po najlepszym limicie, schodząc w głąb
  książki (duże zlecenia przesuwają cenę); Limit (TP) czeka w książce.

### 4. Mechanizm adaptacji FV (prosta zmiana po zamknięciu)

* **Trafiony TP (wygrana)**: `FV = FV + U(0.1, 0.9) · (cena_TP − FV)`.
* **Trafiony SL (przegrana)**: `FV = FV + U(0.1, 0.9) · (cena_SL − FV)`.

Losowy ułamek 10–90% dystansu wprowadza heterogeniczność szybkości adaptacji.

### 5. Wymuszenie akcji (brak martwych tur)

Gdy w turze żaden agent nie złożył zlecenia Market, system wybiera płaskiego
agenta o najmniejszym `|dist|` (wciąż poniżej progu) i zmusza go do otwarcia
pozycji minimalnym wolumenem (1 lot). Dodatkowo, gdyby *wszyscy* agenci byli
w pozycji, wymuszane jest zamknięcie agenta najbliższego progowi SL/TP –
gwarantuje to ciągłość notowań.

### 6. Drzewo decyzyjne tury

1. **Replenish** – uzupełnienie bufora do 100 poziomów po każdej stronie.
2. **SL/TP** – sprawdzenie przebicia SL (Market); TP czeka jako Limit w książce.
3. **Decyzje agentów** – wejścia Market dla `|dist| > próg`.
4. **Wymuszenie akcji** – jeśli krok 3 nie dał zleceń.
5. **Matching** – dopasowanie Market do Limitów; nowa cena = VWAP tury.
6. **Adaptacja** – aktualizacja FV agentów, których TP/SL zrealizowano.
7. **Zapis** – cena, wolumen, statystyki.

## Obserwowane zachowania emergentne

* **Cena wędruje bez średniej powrotnej** – nie ma przyciągania do ceny startowej;
  dryf wynika wyłącznie z interakcji zleceń i adaptacji FV.
* **Konsensus FV podąża za ceną z opóźnieniem** (średnie FV jest opóźnionym
  odbiciem ceny), a rozpiętość percepcji pozostaje szeroka – rynek nie osiąga
  pełnego konsensusu.
* **Żadna tura nie jest martwa** – wymuszenie akcji daje wolumen > 0 w każdej turze.
* Duże zlecenia przechodzą przez wiele poziomów bufora i przesuwają cenę o kilka
  ticków w jednej turze (mikrostruktura oddaje „głębokość" rynku).

## Wyjście

* `summarize()` – podsumowanie: cena start/koniec, min/max, dryf, wolumen, liczba
  transakcji, martwe tury.
* `write_csv()` – pełna historia do CSV (cena, wolumen, spread, open interest,
  średnie/min/max FV).
* `plot()` – 4 panele: cena + śr. FV, wolumen, spread, rozpiętość FV.

## Pomysły na rozwój

* agenci pasywni składający limity wg własnych FV (nie tylko pasywny bufor),
* kwantyzacja limitów do siatki ticków, aukcja otwarcia/zamknięcia,
* profile wolumenu (TPO) i inne wskaźniki AMT,
* heatmapa rozkładu FV w czasie (konsensus jako pasmo, nie punkt).
