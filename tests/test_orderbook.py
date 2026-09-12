"""Testy jednostkowe OrderBooka (logika z logic.md 1.1-1.3)."""

from __future__ import annotations

import random

import pytest

from market_sim.core.order import Side
from market_sim.core.orderbook import OrderBook, Rejected

TICK = 0.25


def make_book(**kwargs) -> OrderBook:
    """Orderbook z deterministycznym refillem (rozmiar zawsze 10)."""
    book = OrderBook(tick_size=TICK, rng=random.Random(1234), **kwargs)
    book.sample_order_size = lambda: 10
    return book


# ----------------------------------------------------------------------
# add_limit
# ----------------------------------------------------------------------
def test_add_limit_nieprzecinajacy_trafia_do_booka():
    book = make_book()

    filled, remaining = book.add_limit(Side.BUY, 100.0, 5, 1.0)

    assert (filled, remaining) == (0, 5)
    assert book.best_bid == 100.0
    assert book.best_ask is None
    assert book.bids[100.0].total_size == 5


def test_add_limit_best_bid_best_ask():
    book = make_book()
    book.add_limit(Side.BUY, 99.5, 3, 1.0)
    book.add_limit(Side.BUY, 100.0, 4, 2.0)
    book.add_limit(Side.SELL, 101.0, 7, 3.0)

    assert book.best_bid == 100.0
    assert book.best_ask == 101.0
    assert book.mid_price() == pytest.approx(100.5)


def test_add_limit_zaokragla_cene_do_ticku():
    book = make_book()

    book.add_limit(Side.BUY, 100.1, 5, 1.0)

    assert list(book.bids) == [100.0]


def test_add_limit_odrzuca_rozmiar_niedodatni():
    book = make_book()

    with pytest.raises(Rejected):
        book.add_limit(Side.BUY, 100.0, 0, 1.0)
    with pytest.raises(Rejected):
        book.add_limit(Side.SELL, 100.0, -3, 1.0)


def test_add_limit_odrzuca_cene_niedodatnia():
    book = make_book()

    with pytest.raises(Rejected):
        book.add_limit(Side.BUY, 0.0, 5, 1.0)
    with pytest.raises(Rejected):
        book.add_limit(Side.BUY, -1.0, 5, 1.0)


def test_add_limit_odrzuca_duplikat_id():
    book = make_book()
    book.add_limit(Side.BUY, 99.0, 5, 1.0, order_id=42)

    with pytest.raises(Rejected):
        book.add_limit(Side.BUY, 98.0, 5, 1.0, order_id=42)


def test_add_limit_sumuje_rozmiary_na_poziomie():
    book = make_book()
    book.add_limit(Side.BUY, 100.0, 5, 1.0)
    book.add_limit(Side.BUY, 100.0, 7, 2.0)

    assert len(book.bids) == 1
    assert book.bids[100.0].total_size == 12
    assert [o.size for o in book.bids[100.0].orders] == [5, 7]


def test_add_limit_cena_rowna_best_ask_jest_agresywna():
    book = make_book()
    book.add_limit(Side.SELL, 100.25, 5, 1.0)

    filled, remaining = book.add_limit(Side.BUY, 100.25, 3, 2.0)

    assert (filled, remaining) == (3, 0)
    assert book.best_ask == 100.25  # reszta 2 szt. wciąż tam leży
    assert book.asks[100.25].total_size == 2
    assert book.history[-1].price == 100.25
    assert book.history[-1].size == 3
    assert book.history[-1].aggressor_side is Side.BUY


def test_add_limit_agresywny_reszte_odklada_po_swojej_stronie():
    book = make_book()
    book.add_limit(Side.BUY, 99.5, 4, 1.0)
    book.add_limit(Side.SELL, 100.0, 2, 2.0)

    filled, remaining = book.add_limit(Side.BUY, 100.0, 10, 3.0)

    assert (filled, remaining) == (2, 8)
    assert book.best_ask is None
    assert book.best_bid == 100.0  # best_bid + tick
    assert book.bids[100.0].total_size == 8
    assert book.history[-1].price == 100.0
    assert book.history[-1].size == 2


def test_add_limit_sell_agresywny_wykonuje_sie_po_bidach():
    book = make_book()
    book.add_limit(Side.BUY, 100.0, 6, 1.0)

    filled, remaining = book.add_limit(Side.SELL, 99.5, 4, 2.0)

    assert (filled, remaining) == (4, 0)
    assert book.bids[100.0].total_size == 2


# ----------------------------------------------------------------------
# execute_market
# ----------------------------------------------------------------------
def test_execute_market_zerowy_rozmiar_to_noop():
    book = make_book()
    book.add_limit(Side.SELL, 100.0, 5, 1.0)

    assert book.execute_market(Side.BUY, 0, 2.0) == (0, 0)
    assert book.history == []
    assert book.asks[100.0].total_size == 5


def test_execute_market_fifo_w_obrębie_poziomu():
    book = make_book()
    book.add_limit(Side.SELL, 100.0, 3, 1.0)
    book.add_limit(Side.SELL, 100.0, 4, 1.0)

    filled, remaining = book.execute_market(Side.BUY, 5, 2.0)

    assert (filled, remaining) == (5, 0)
    assert [t.size for t in book.history] == [3, 2]
    assert book.asks[100.0].total_size == 2
    assert len(book.asks[100.0].orders) == 1
    assert book.asks[100.0].orders[0].size == 4
    assert book.asks[100.0].orders[0].remaining == 2


def test_execute_market_przechodzi_przez_wiele_poziomow():
    book = make_book()
    book.add_limit(Side.SELL, 100.0, 2, 1.0)
    book.add_limit(Side.SELL, 100.25, 2, 1.0)
    book.add_limit(Side.SELL, 100.5, 2, 1.0)

    filled, remaining = book.execute_market(Side.BUY, 5, 2.0)

    assert (filled, remaining) == (5, 0)
    assert [t.price for t in book.history] == [100.0, 100.25, 100.5]
    assert [t.size for t in book.history] == [2, 2, 1]
    assert book.best_ask == 100.5
    assert 100.0 not in book.asks
    assert 100.25 not in book.asks


def test_execute_market_czyszczenie_poziomu_i_best_ask():
    book = make_book()
    book.add_limit(Side.SELL, 100.0, 3, 1.0)
    book.add_limit(Side.SELL, 100.25, 3, 1.0)

    book.execute_market(Side.BUY, 3, 2.0)

    assert 100.0 not in book.asks
    assert book.best_ask == 100.25


def test_execute_market_brak_plynnosci_cancel():
    book = make_book(partial_fill_mode="cancel")

    filled, remaining = book.execute_market(Side.BUY, 5, 1.0)

    assert (filled, remaining) == (0, 5)
    assert book.bids == {}
    assert book.asks == {}
    assert book.history == []


def test_execute_market_brak_plynnosci_synthetic():
    book = make_book(partial_fill_mode="synthetic", mid_price=100.0)
    book.add_limit(Side.BUY, 100.0, 1, 1.0)

    filled, remaining = book.execute_market(Side.SELL, 5, 2.0)

    # 1 szt. zjedzona z bida, reszta 4 szt. odłożona jako ask (best_bid + tick)
    assert (filled, remaining) == (1, 4)
    assert book.asks[100.25].total_size == 4


def test_execute_market_brak_plynnosci_panic_domyka_przez_refill():
    book = make_book(partial_fill_mode="panic", mid_price=100.0)

    filled, remaining = book.execute_market(Side.BUY, 5, 1.0)

    assert (filled, remaining) == (5, 0)
    assert book.best_ask is not None
    assert book.history != []


def test_execute_market_nie_lamie_inwariantu():
    book = make_book()
    for i in range(10):
        book.add_limit(Side.SELL, 100.0 + i * TICK, 4, float(i))

    book.execute_market(Side.BUY, 13, 99.0)

    assert book.best_bid is None
    assert book.best_ask == 100.75


# ----------------------------------------------------------------------
# refill
# ----------------------------------------------------------------------
def test_refill_tworzy_minimalna_glebokosc_po_obu_stronach():
    book = make_book(mid_price=100.0)

    book.refill(min_ticks=20)

    assert len(book.bids) == 20
    assert len(book.asks) == 20
    assert book.best_bid == 99.75
    assert book.best_ask == 100.25
    assert book.best_bid < book.best_ask


def test_refill_rozmiary_maleja_z_odlegloscia():
    book = make_book(mid_price=100.0)

    book.refill(min_ticks=20)

    sizes = [level.total_size for _, level in reversed(book.bids.items())]
    assert sizes == sorted(sizes, reverse=True)
    assert all(size >= 1 for size in sizes)


def test_refill_nie_duplikuje_istniejacych_poziomow():
    book = make_book(mid_price=100.0)
    book.add_limit(Side.BUY, 99.75, 5, 1.0)
    book.add_limit(Side.SELL, 100.25, 5, 1.0)

    book.refill(min_ticks=20)

    assert book.bids[99.75].total_size == 5
    assert book.asks[100.25].total_size == 5
    assert len(book.bids) == 20
    assert len(book.asks) == 20


def test_refill_pomija_gdy_glebokosc_wystarcza():
    book = make_book(mid_price=100.0)
    book.refill(min_ticks=3)
    bids_before = dict(book.bids)

    book.refill(min_ticks=3)

    assert dict(book.bids) == bids_before


def test_refill_jednostronny_anchor_od_best_przeciwnej_strony():
    book = make_book()
    book.add_limit(Side.SELL, 100.0, 1, 1.0)

    book.refill(min_ticks=3)

    assert book.best_ask == 100.0
    assert list(book.bids) == [99.25, 99.5, 99.75]
    assert book.best_bid < book.best_ask


def test_refill_wymusza_minimalny_rozmiar_jeden():
    book = OrderBook(
        tick_size=TICK, refill_decay_tau=0.01, mid_price=100.0, rng=random.Random(7)
    )

    book.refill(min_ticks=20)

    assert all(level.total_size >= 1 for level in book.bids.values())
    assert all(level.total_size >= 1 for level in book.asks.values())


# ----------------------------------------------------------------------
# mid_price / ensure_spread
# ----------------------------------------------------------------------
def test_mid_price_pusty_book_uzywa_ceny_startowej():
    book = OrderBook(tick_size=TICK, mid_price=50.0)

    assert book.mid_price() == 50.0


def test_mid_price_jednostronny_book():
    book = make_book()
    book.add_limit(Side.BUY, 100.0, 1, 1.0)

    assert book.mid_price() == pytest.approx(100.25)


def test_ensure_spread_rozdziela_skrzyzowane_best():
    book = make_book()
    book.add_limit(Side.BUY, 100.0, 2, 1.0)
    book.add_limit(Side.SELL, 100.5, 2, 1.0)
    # ręcznie skrzyżowany book: bid powyżej ask
    book.bids[100.75] = book.bids.pop(100.0)
    book.bids[100.75].price = 100.75

    book.ensure_spread()

    assert book.best_bid < book.best_ask
    assert book.best_ask == 100.5
