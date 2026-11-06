import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from appro.data.schemas import TABLES, coerce  # noqa: E402
from appro.data.sources import LocalCsvSource  # noqa: E402

SEED = ROOT / "data" / "seed"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def seed_source() -> LocalCsvSource:
    """The seed CSV files (reference tables and facts alike), as an engine-ready table provider."""
    return LocalCsvSource(SEED)


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


def seed_frame(name: str) -> pd.DataFrame:
    return coerce(pd.read_csv(SEED / f"{name}.csv", dtype=str, keep_default_na=False), TABLES[name])
