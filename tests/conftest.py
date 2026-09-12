"""Wspólne fixture testów."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from market_sim.config import Config, load_config

TMP_ROOT = Path(__file__).resolve().parents[1] / ".pytest_tmp"


@pytest.fixture
def config() -> Config:
    """Konfiguracja z config.yaml (bez modyfikacji)."""
    return load_config()


@pytest.fixture
def small_config(config: Config) -> Config:
    """Konfiguracja do testów: krótki dzień, niezależna od config.yaml."""
    short = config.model_copy(deep=True)
    short.simulation.turns_per_day = 300
    short.simulation.days_to_generate = 1
    return short


@pytest.fixture
def workspace_tmp() -> Path:
    """Katalog tymczasowy w workspace (katalog systemowy bywa niedostępny)."""
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    index = 0
    while (TMP_ROOT / f"test-{index}").exists():
        index += 1
    path = TMP_ROOT / f"test-{index}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
