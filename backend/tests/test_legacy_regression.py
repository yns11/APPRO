"""Regression against the cached values of the legacy Excel workbook.

The legacy semantics are reproduced with the corresponding engine parameters:

* weekly plan spread with ``per_day`` rounding (``ROUND(week / 5)`` on Monday–Friday);
* receipts replace same-day orders, past orders without receipt count as delivered
  (we translate that into explicit receipts for the test);
* calendar-day coverage where a tie is *not* counted as covered.
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest

from appro.data.assembler import erp_dataset
from appro.engine import run_mrp
from appro.engine.models import EngineParams, OrderStatus, Receipt, StockSnapshot


@pytest.fixture(scope="module")
def legacy(fixtures_dir):
    return json.loads((fixtures_dir / "legacy_expected.json").read_text())


@pytest.fixture(scope="module")
def legacy_result(seed_source, legacy):
    ds = erp_dataset(seed_source)
    start = dt.date.fromisoformat(legacy["start_date"])
    # snapshot = legacy initial stock at the first grid day
    ds.stock = [StockSnapshot(aid, start, a["initial_stock"]) for aid, a in legacy["articles"].items()]
    # legacy rule: a past order with no explicit receipt was counted as delivered on its day
    linked = {r.order_id for r in ds.receipts if r.order_id}
    extra = []
    for o in ds.orders:
        if o.status == OrderStatus.RECEIVED and o.order_id not in linked:
            extra.append(Receipt(f"LEG-{o.order_id}", o.article_id, o.expected_date, o.qty_ordered, o.supplier_id, o.order_id))
        if o.status in (OrderStatus.RECEIVED, OrderStatus.PARTIAL, OrderStatus.CANCELLED):
            continue
    ds.receipts.extend(extra)
    for o in ds.orders:
        if o.status == OrderStatus.OPEN:
            o.qty_received = 0.0
    params = EngineParams(as_of=start + dt.timedelta(days=1), horizon_days=len(legacy["dates"]) - 2, history_days=1,
                          spread_rounding="per_day", coverage_unit="calendar", coverage_tie_rule="not_covered",
                          late_order_policy="keep", firm_sources=("FIRM", "FORECAST"), generate_proposals=False)
    return run_mrp(ds, params), start


def test_daily_demand_matches_excel(legacy, legacy_result):
    result, start = legacy_result
    for aid, exp in legacy["articles"].items():
        got = result.articles[aid]
        idx = got.dates.index(start)
        mine = np.array(got.demand[idx: idx + len(exp["besoin"])])
        theirs = np.array(exp["besoin"])
        assert np.allclose(mine, theirs, atol=0.01), f"{aid}: demand differs (max diff {np.abs(mine - theirs).max()})"


def test_projected_stock_matches_excel(legacy, legacy_result):
    result, start = legacy_result
    for aid, exp in legacy["articles"].items():
        got = result.articles[aid]
        idx = got.dates.index(start)
        mine = np.array(got.stock_firm[idx: idx + len(exp["stock"])])
        theirs = np.array([v for v in exp["stock"]], dtype=float)
        assert np.allclose(mine, theirs, atol=0.05), f"{aid}: stock differs (max diff {np.abs(mine - theirs).max()})"


def test_coverage_matches_excel(legacy, legacy_result):
    result, start = legacy_result
    mismatches = []
    for aid, exp in legacy["articles"].items():
        got = result.articles[aid]
        for day, cov in exp["couverture"].items():
            d = dt.date.fromisoformat(day)
            i = got.dates.index(d)
            # the legacy coverage is capped by its own (870-day) grid; ours by the test horizon
            expected = min(cov, len(got.dates) - 1 - i)
            if got.coverage_firm[i] != expected:
                mismatches.append((aid, day, got.coverage_firm[i], expected))
    assert not mismatches, mismatches[:10]


def test_target_stock_matches_excel(legacy, legacy_result):
    result, start = legacy_result
    for aid, exp in legacy["articles"].items():
        got = result.articles[aid]
        for day, target in exp["target"].items():
            i = got.dates.index(dt.date.fromisoformat(day))
            if i + got.article.coverage_target_days > len(got.dates) - 1:
                continue  # legacy window is longer than the test horizon
            assert abs(got.target_stock[i] - target) < 0.05, (aid, day, got.target_stock[i], target)
