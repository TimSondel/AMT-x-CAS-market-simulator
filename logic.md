CZĘŚĆ 1 — Drzewa decyzyjne (pseudokod)
1.1. OrderBook — dodanie zlecenia LIMIT
text
FUNCTION add_limit(side, price, size, timestamp):
    # --- walidacja wejścia ---
    IF size <= 0: RETURN Rejected
    IF price nie jest wielokrotnością tick_size: 
        price = round_to_tick(price)
    
    # --- normalizacja ceny do zakresu booka ---
    IF price <= 0: RETURN Rejected
    
    # --- sprawdzenie przecięcia z drugą stroną (crossed book) ---
    IF side == BUY AND best_ask exists AND price >= best_ask:
        # zlecenie limit staje się agresywne → traktuj jak market z limitem
        filled, remaining = match_against_asks(price, size, timestamp)
        IF remaining > 0:
            rest_as_limit(BUY, best_bid + tick, remaining, timestamp)
        RETURN PartiallyFilled(filled, remaining)
    
    IF side == SELL AND best_bid exists AND price <= best_bid:
        filled, remaining = match_against_bids(price, size, timestamp)
        IF remaining > 0:
            rest_as_limit(SELL, best_ask - tick, remaining, timestamp)
        RETURN PartiallyFilled(filled, remaining)
    
    # --- zwykłe dodanie do booka ---
    level = get_or_create_level(side, price)
    level.total_size += size
    level.orders.append(Order(...))
    update_best(side)
    
    # --- inwariant: brak przecięcia ---
    ASSERT best_bid < best_ask
    RETURN Accepted
Przypadki brzegowe:

cena dokładnie równa best_ask (BUY) → agresywne

cena ułamkowa → zaokrąglenie do tick

cena poza widełkami (np. 0 lub ujemna) → odrzucone

rozmiar 0 lub ujemny → odrzucone

duplikat zlecenia (jeśli śledzisz ID) → odrzucone

1.2. OrderBook — wykonanie zlecenia MARKET
text
FUNCTION execute_market(side, size, timestamp):
    IF size <= 0: RETURN NoOp
    
    opposite = (side == BUY) ? asks : bids
    
    filled_total = 0
    WHILE size > 0 AND opposite not empty:
        best_level = opposite.best()
        
        # --- ile możemy zjeść z tego poziomu ---
        take = min(size, best_level.total_size)
        
        # --- FIFO w obrębie poziomu ---
        remaining_take = take
        WHILE remaining_take > 0 AND best_level.orders not empty:
            order = best_level.orders[0]        # najstarsze
            consume = min(remaining_take, order.remaining)
            order.remaining -= consume
            remaining_take -= consume
            fill_price = best_level.price
            
            emit_trade(side, fill_price, consume, timestamp)
            
            IF order.remaining == 0:
                best_level.orders.pop(0)
        
        best_level.total_size -= take
        size -= take
        filled_total += take
        
        # --- poziom pusty → usuń i zaktualizuj best ---
        IF best_level.total_size == 0:
            opposite.remove_level(best_level.price)
            update_best(side == BUY ? SELL : BUY)
    
    # --- brak płynności: zachowanie zależne od konfiguracji ---
    IF size > 0:
        IF config.partial_fill_mode == "cancel":
            pass                                # reszta przepada
        ELIF config.partial_fill_mode == "synthetic":
            # dopisz do booka po stronie przeciwnej i zjedz ponownie po refillu
            queue_for_next_step(side, size)
        ELIF config.partial_fill_mode == "panic":
            # wywołaj refill po stronie przeciwnej i dokończ
            refill_side(opposite_side_of(side))
            execute_market(side, size, timestamp)  # rekurencja kontrolowana
    
    RETURN Filled(filled_total, size)
Przypadki brzegowe:

jedna strona booka pusta → obsłuż przez partial_fill_mode

zlecenie market większe niż cała płynność → patrz wyżej

poziom pusty po częściowym zjedzeniu → usuń i przesuń best

wiele poziomów w jednym skoku → pętla WHILE

zlecenie market o rozmiarze 0 → NoOp (żeby nie psuć statystyk)

1.3. OrderBook — REFILL po zjedzeniu
text
FUNCTION refill(min_ticks=20):
    FOR side IN [BID, ASK]:
        # --- ustal punkt startowy ---
        IF side == BID: anchor = best_bid OR mid_price − tick
        ELSE:           anchor = best_ask OR mid_price + tick
        
        # --- kierunek rozszerzania ---
        step = (side == BID) ? −tick : +tick
        
        # --- ile poziomów brakuje do min_ticks? ---
        current_levels = count_levels(side, from_price=anchor, 
                                       direction=step, within=min_ticks)
        missing = min_ticks − current_levels
        
        IF missing <= 0: CONTINUE
        
        # --- rozkład wielkości na poziomach maleje z odległością ---
        FOR i IN 1..missing:
            price = anchor + i * step
            IF level_exists(side, price): CONTINUE
            
            # --- skala zależy od widoczności i spreadu ---
            base_size = sample_order_size()
            decay     = exp(−i / config.refill_decay_tau)
            visibility_mult = (side zgodna z percepcją) 
                              ? config.vis_market_mult 
                              : config.vis_limit_mult
            
            size = max(1, round(base_size * decay * visibility_mult))
            add_limit(side, price, size, now())
    
    # --- inwariant ---
    ASSERT best_bid < best_ask
    ASSERT count_levels(BID) >= min_ticks − tolerance
    ASSERT count_levels(ASK) >= min_ticks − tolerance
Przypadki brzegowe:

obie strony puste (start symulacji) → użyj mid_price jako anchor

tylko jedna strona pusta → anchor z best przeciwnej strony ± tick

poziom już istnieje → nie duplikuj

missing == 0 → skip (nie marnuj RNG)

bardzo mały decay → bardzo małe rozmiary → wymuś min_size = 1

1.4. OrderBook — punkty decyzyjne (mapa)
text
                        add_limit / execute_market
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
              LIMIT                           MARKET
                │                               │
       ┌────────┴────────┐               ┌──────┴──────┐
       ▼                 ▼               ▼             ▼
  cena poprawna?   krzyżuje się?   jest płynność?  brak płynności
       │                 │               │             │
       nie → reject    tak → match    tak → fill   partial_fill_mode
                        │                             │
                        ▼                        ┌────┴────┐
                    reszta →                    ▼    ▼    ▼
                    rest_as_limit            cancel synth panic
1.5. Simulation — krok symulacji
text
FUNCTION step(t):
    # --- 1. KROK CZASU ---
    advance_time(config.step_seconds)   # domyślnie 0.25s
    
    # --- 2. AKTUALIZACJA PROCESÓW (tylko co krok) ---
    fair_value.update()          # OU + jump + regime
    spread.update()              # OU-lognormal
    certainty.update()           # log-OU → clip
    visibility.update()          # Beta / logit-OU
    regime.maybe_transition()    # Markov
    
    # --- 3. WOLUMEN INTRADAY ---
    t_norm = (t − day_start) / (day_end − day_start)
    volume_mult = intraday_curve(t_norm)          # U-shape
    
    IF is_overnight(t):
        volume_mult *= config.overnight_factor
        IF random() > config.overnight_activity:   # np. 0.3
            RETURN  # krok „cichy", brak zleceń
    
    # --- 4. PERCEPCJA → KIERUNEK ---
    mid = orderbook.mid_price()
    z   = (fair_value − mid) / max(spread, tick)
    p_long = sigmoid(certainty * z)
    direction = random() < p_long ? BUY : SELL
    
    # --- 5. WYBÓR TYPU ZLECENIA: LIMIT vs MARKET ---
    v = visibility                                       # 0=limit, 1=market
    use_market = random() < v * config.market_bias
    
    IF use_market:
        # --- 5a. MARKET ORDER ---
        size = sample_order_size() * volume_mult * certainty
        size = max(1, round(size))
        orderbook.execute_market(direction, size, t)
        
        # --- 5b. REFILL po market orderze ---
        orderbook.refill(min_ticks=20)
    
    ELSE:
        # --- 5c. LIMIT ORDER ---
        # kierunek limitu: zgodny z percepcją → po stronie przeciwnej do ruchu
        limit_side = (direction == BUY) ? BID : ASK
        
        # odległość od mid zależy od spreadu i pewności
        offset_ticks = sample_offset(spread, certainty)   # np. 1..spread
        price = mid + (limit_side == BID ? −offset : +offset) * tick
        price = round_to_tick(price)
        
        # wielkość skalowana widocznością (jeśli limit „widzi" percepcję)
        size = sample_order_size() * volume_mult
        IF limit_side zgodna z percepcją:
            size *= config.vis_limit_mult
        size = max(1, round(size))
        
        orderbook.add_limit(limit_side, price, size, t)
        # limit nie wymaga refillu (sam go dopełnia), ale sprawdź inwariant
        orderbook.ensure_spread()
    
    # --- 6. ZDARZENIA LOSOWE (opcjonalne) ---
    IF random() < config.cancel_rate:
        orderbook.cancel_random_order()
    IF random() < config.flash_crash_rate:
        orderbook.execute_market(SELL, config.flash_size, t)
        orderbook.refill()
    
    # --- 7. SNAPSHOT (dla świec wolumenowych) ---
    emit_snapshot(mid, best_bid, best_ask, t)
Przypadki brzegowe:

z == 0 → p_long = 0.5

spread == 0 → zabezpiecz dzielenie (max(spread, tick))

overnight → losowe wyciszenie kroku

volume_mult == 0 → nie generuj zleceń

direction zgodny z limit po tej samej stronie co mid → wymuś offset ≥ 1 tick

brak mid (book pusty) → awaryjny refill z ceny startowej

1.6. Simulation — cykl dnia
text
FUNCTION run_day(day_index):
    # --- 1. INICJALIZACJA DNIA ---
    reset_intraday_processes()
    orderbook.ensure_min_depth(20)
    day_open_price = orderbook.mid_price()
    
    # --- 2. PĘTLA TUR ---
    FOR t IN 1..config.turns_per_day:
        step(t)
        
        # --- przerwij dzień jeśli katastrofa (sanity) ---
        IF orderbook.mid_price() <= 0:
            log_error("mid <= 0, abort day")
            BREAK
    
    # --- 3. END OF DAY CLEANUP ---
    day_close_price = orderbook.mid_price()
    close_open_positions(pct=config.eod_close_pct)
    orderbook.thin_to(ticks=config.eod_keep_ticks)   # np. 20
    
    # --- 4. AGREGACJA ---
    save_day_candles(day_index)
    save_day_profile(day_index)
    save_day_trades(day_index)
    save_day_meta(day_index, {
        open: day_open_price, close: day_close_price,
        high, low, total_volume, open_positions_after
    })
1.7. Simulation — EOD (koniec dnia)
text
FUNCTION close_open_positions(pct):
    # --- 1. USTAL OTWARTE POZYCJE ---
    open_positions = ledger.get_open()    # z historii trade'ów
    n_close = round(len(open_positions) * pct)
    to_close = random.sample(open_positions, n_close)
    
    # --- 2. WSTRZYKNIJ PŁYNNOŚĆ ZAMYKAJĄCĄ ---
    mid = orderbook.mid_price()
    FOR side IN [BID, ASK]:
        FOR i IN 1..config.eod_limit_levels:      # np. 5 poziomów
            price = mid + (side==BID ? −i : +i) * tick
            orderbook.add_limit(side, price, config.eod_limit_size, now())
    
    # --- 3. ZAMKNIJ PROCENT POZYCJI ---
    FOR pos IN to_close:
        close_side = (pos.direction == LONG) ? SELL : BUY
        orderbook.execute_market(close_side, pos.size, now())
        ledger.mark_closed(pos)
    
    # --- 4. WYCZYŚĆ „PRZEJŚCIOWE" LIMITY EOD ---
    orderbook.remove_tagged("eod")   # te wstrzyknięte w kroku 2
    
    # --- 5. UZUPEŁNIJ DO MINIMALNEJ GŁĘBOKOŚCI ---
    orderbook.refill(min_ticks=config.overnight_depth)   # np. 20
Przypadki brzegowe:

open_positions == 0 → skip

pct == 0 → nic nie zamykaj

pct == 1 → zamknij wszystko

brak płynności po stronie zamykania → użyj panic mode

overnight gap: nie generuj — następny dzień startuje z ostatniej ceny

1.8. Simulation — pełne drzewo decyzyjne
text
                          run_day(d)
                               │
                    ┌──────────┴──────────┐
                    ▼                     ▼
              init dnia              pętla tur (N)
                                          │
                              ┌───────────┴────────────┐
                              ▼                        ▼
                       aktualizuj procesy         volume curve
                              │                        │
                              ▼                        ▼
                       percepcja → kierunek      overnight?
                              │                        │
                              ▼                   ┌────┴────┐
                       visibility → typ          ▼         ▼
                              │                 cichy    normalny
                       ┌──────┴──────┐
                       ▼             ▼
                    MARKET        LIMIT
                       │             │
                       ▼             ▼
                  execute → refill   add_limit
                       │             │
                       └──────┬──────┘
                              ▼
                       snapshot / trade
                              │
                              ▼
                       koniec pętli?
                              │
                              ▼
                          EOD cleanup
                              │
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼
                 close %   thin book  save
CZĘŚĆ 2 — Dokumentacja strukturalna
2.1. Widok ogólny (docelowy wygląd UI)
text
┌──────────────────────────────────────────────────────────────────┐
│  [Menu] Plik  Symulacja  Widok  Pomoc                            │
├──────────────────────────────────────────────────────────────────┤
│  [Pasek sterowania]                                              │
│  ▶ Play  ⏸ Pauza  ⏭ Krok  ⏩ Prędkość: [1x ▼]                    │
│  Dzień: [◀ 3/47 ▶]   Seed: [____]   [Nowa symulacja] [Zapisz]    │
├──────────────────────────────┬───────────────────────────────────┤
│                              │                                   │
│   WYKRES ŚWIEC WOLUMENOWYCH  │   VOLUME PROFILE (dla dnia)       │
│                              │                                   │
│         │                    │        ▓                          │
│        ┃│┃                   │       ▓▓▓                         │
│       ┃ │ ┃                  │      ▓▓▓▓▓                        │
│      ┃  │  ┃                 │     ▓▓▓▓▓▓▓                       │
│     ┃   │   ┃                │    ▓▓▓▓▓▓▓▓▓                      │
│    ┃    │    ┃               │     ▓▓▓▓▓▓▓                       │
│   ┃     │     ┃              │      ▓▓▓▓▓                        │
│                              │       ▓▓▓                         │
│   (przewijanie: ← →, zoom)   │        ▓                          │
│                              │  ── POC ──                        │
│                              │  ── VAH ──                        │
│                              │  ── VAL ──                        │
├──────────────────────────────┴───────────────────────────────────┤
│  [Status] Dzień 3/47 | Tura 1200/2000 | Mid: 4523.75 | Vol: 12k  │
└──────────────────────────────────────────────────────────────────┘

Przepływ danych: 
config.yaml ──► ConfigLoader ──► Simulation
                                   │
                                   ▼
                             OrderBook ◄── Processes (OU/jump/regime)
                                   │           ▲
                                   │           │
                                   ▼           │
                              Trade stream ────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
              CandleBuilder  ProfileBuilder   DayManager
                    │              │              │
                    └──────┬───────┴──────┬───────┘
                           ▼              ▼
                       HDF5Writer    UI (signals)
                           │              │
                           ▼              ▼
                     simulation.h5   MainWindow
                           │
                           ▼
                       HDF5Reader ──► UI (replay)