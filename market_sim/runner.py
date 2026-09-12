"""Uruchomienie symulacji: generacja dnia, agregacja i zapis do HDF5."""

from __future__ import annotations

import argparse
from pathlib import Path

from market_sim.config import Config, load_config
from market_sim.simulation import DayResult, Simulation
from market_sim.storage.aggregate import CandleBuilder, build_volume_profile
from market_sim.storage.writer import HDF5Writer


def generate_day(
    simulation: Simulation, day_index: int
) -> tuple[DayResult, list, object]:
    """Generuje dzień i buduje jego świece wolumenowe oraz volume profile."""
    day = simulation.run_day(day_index)
    builder = CandleBuilder(
        target_volume=simulation.config.volume.candle_target_volume,
        day_index=day_index,
    )
    candles = builder.add_trades(day.trades)
    candles = builder.finish()
    profile = build_volume_profile(
        day.trades, simulation.config.tick_size, day_index=day_index
    )
    return day, candles, profile

def run(
    config: Config | None = None,
    days: int | None = None,
    seed: int | None = None,
    output: str | Path = "simulation.h5",
    verbose: bool = False,
) -> dict:
    """Generuje `days` dni symulacji i zapisuje je do pliku HDF5."""
    config = config or load_config()
    days = config.simulation.days_to_generate if days is None else days
    simulation = Simulation(config, seed=seed)
    writer = HDF5Writer(output, config, simulation.seed)

    results = []
    for day_index in range(days):
        day, candles, profile = generate_day(simulation, day_index)
        writer.write_day(day, candles, profile)
        results.append(day)
        if verbose:
            print(
                f"dzien {day_index}: tury={config.turns_per_day} "
                f"trades={day.num_trades} wolumen={day.total_volume} "
                f"markety={day.market_orders} limity={day.limit_orders} "
                f"odrzucone={day.rejected_limits} swiece={len(candles)} "
                f"open={day.open_price} close={day.close_price} "
                f"high={day.high} low={day.low} "
                f"pozycje={day.open_positions_after}"
            )
    return {
        "days": results,
        "output": str(output),
        "seed": simulation.seed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generator symulacji rynkowej")
    parser.add_argument("--config", default=None, help="ścieżka do config.yaml")
    parser.add_argument("--days", type=int, default=None, help="liczba dni")
    parser.add_argument("--seed", type=int, default=None, help="seed RNG")
    parser.add_argument(
        "--output", default="simulation.h5", help="plik wyjściowy HDF5"
    )
    parser.add_argument("--quiet", action="store_true", help="bez podsumowania")
    args = parser.parse_args(argv)

    run(
        config=load_config(args.config),
        days=args.days,
        seed=args.seed,
        output=args.output,
        verbose=not args.quiet,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
