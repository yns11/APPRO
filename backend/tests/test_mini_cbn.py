"""The mini CBN notebook (docs/mini_cbn.ipynb) reproduces the engine exactly in its simplified frame :
one supplier, working days S1.1 … S10.5, a demand per day, a stock at the end of yesterday, no firm order,
no plan cell, no refusal, no cap.  The function cell of the notebook is executed here and compared with
``run_mrp`` on fixed and random scenarios."""
from __future__ import annotations

import datetime as dt
import json
import random
from pathlib import Path

import pytest

from appro.engine import run_mrp
from appro.engine.models import (
    Article,
    BomLine,
    ConsumptionLine,
    Dataset,
    EngineParams,
    PdpLine,
    Program,
    StockSnapshot,
    Supplier,
    SupplierLink,
)

NOTEBOOK = Path(__file__).resolve().parents[2] / "docs" / "mini_cbn.ipynb"
MON = dt.date(2026, 9, 21)  # S1.1
D = dt.timedelta


@pytest.fixture(scope="module")
def mini():
    pytest.importorskip("pandas")
    nb = json.loads(NOTEBOOK.read_text())
    code = [c for c in nb["cells"] if c["cell_type"] == "code"][0]["source"]
    ns: dict = {}
    exec("".join(code) if isinstance(code, list) else code, ns)
    return ns


def date_of(i: int) -> dt.date:
    return MON + D(weeks=i // 5, days=i % 5)


def label_of(d: dt.date) -> str:
    k = (d - MON).days
    return f"S{k // 7 + 1}.{k % 7 + 1}"


def both(mini, per_week: list[float], stock: float, today_idx: int, couverture=5, tolerance=15.0, delai=5, marge=1,
         weekdays=frozenset({1, 2, 3, 4, 5}), strict=False, moq=100, um=50):
    days = mini["jours"](10)
    besoin = {j: per_week[i // 5] / 5 for i, j in enumerate(days)}
    today = date_of(today_idx)
    ds = Dataset(
        articles=[Article("A1", "Widget", "PCE", coverage_target_days=couverture, alert_red_days=2, alert_yellow_days=5, overstock_days=30)],
        suppliers=[Supplier("S1", "Supplier 1", delivery_weekdays=frozenset(weekdays), lead_time_days=delai, strict_days=strict)],
        links=[SupplierLink("A1", "S1", moq=moq, pack_qty=um, quota_pct=100, priority=1)],
        programs=[Program("P1", "Line 1")], bom=[BomLine("P1", "A1", qty_per=1.0)],
        pdp=[PdpLine("P1", MON + D(weeks=w), q) for w, q in enumerate(per_week)],
        consumption=[ConsumptionLine("A1", date_of(i), besoin[days[i]]) for i in range(today_idx)],
        orders=[], receipts=[], stock=[StockSnapshot("A1", today - D(days=1 if today_idx % 5 else 3), stock)],
    )
    ar = run_mrp(ds, EngineParams(as_of=today, horizon_days=(date_of(49) - today).days, target_tolerance_pct=tolerance,
                                  lead_margin_days=marge)).articles["A1"]
    real = [(label_of(p.delivery_date), p.qty, label_of(p.order_date), p.urgent, p.floor, p.off_standard) for p in ar.proposals]
    props, projection = mini["cbn"](besoin, stock, days[today_idx], couverture, tolerance, delai, marge, set(weekdays), strict, moq, um)
    got = [(r.livraison, r.quantite, r.date_limite, r.urgent, r.plancher, r.hors_jour_standard) for r in props.itertuples()]
    assert len(projection) == 50
    return real, got


def test_fixed_scenarios(mini):
    flat = [500.0] * 10
    cases = [
        dict(per_week=flat, stock=1500, today_idx=0, weekdays={1}),
        dict(per_week=flat, stock=1500, today_idx=2, weekdays={1}),
        dict(per_week=flat, stock=1500, today_idx=2, weekdays={1}, strict=True),
        dict(per_week=flat, stock=350, today_idx=0, delai=10),
        dict(per_week=flat, stock=550, today_idx=3, weekdays={4}, delai=10, marge=0),
        dict(per_week=flat, stock=250, today_idx=2, weekdays={2, 4}, delai=4),
        dict(per_week=flat, stock=900, today_idx=0, moq=1000, um=250, delai=3),
        dict(per_week=flat, stock=0, today_idx=0, weekdays={1}, strict=True),
    ]
    for kw in cases:
        real, got = both(mini, **kw)
        assert got == real, kw
    real, got = both(mini, per_week=flat, stock=250, today_idx=2, weekdays={2, 4}, delai=4)
    assert got[0] == ("S1.5", 100.0, "S1.2", True, True, True) and got[1][0] == "S2.1" and got[1][5]   # plancher then ◇


def test_random_scenarios(mini):
    rng = random.Random(7)
    for _ in range(40):
        kw = dict(per_week=[float(rng.choice([0, 250, 500, 750, 1000, 1500])) for _ in range(10)],
                  stock=float(rng.choice([0, 200, 500, 900, 1500])), today_idx=rng.randrange(0, 5),
                  weekdays=rng.choice([{1}, {2, 4}, {1, 2, 3, 4, 5}, {3}]), delai=rng.choice([2, 4, 5, 8, 10]),
                  marge=rng.choice([0, 1, 2]), strict=rng.choice([False, True]), moq=rng.choice([100, 400]), um=rng.choice([50, 100]),
                  couverture=rng.choice([3, 5, 10]), tolerance=rng.choice([0.0, 15.0, 30.0]))
        real, got = both(mini, **kw)
        assert got == real, kw
