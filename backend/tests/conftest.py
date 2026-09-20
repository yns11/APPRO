import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from appro.data.sources import LocalCsvSource  # noqa: E402

SEED = ROOT / "data" / "seed"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def seed_source() -> LocalCsvSource:
    return LocalCsvSource(SEED)


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES
