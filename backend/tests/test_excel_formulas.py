"""The live workbook must compute the same stocks as the engine.

The workbook is recalculated with LibreOffice (headless) when it is available (GitHub's
``ubuntu-latest`` runners ship it); otherwise the test is skipped.
"""
from __future__ import annotations

import datetime as dt
import io
import shutil
import subprocess

import pytest
from openpyxl import load_workbook

from appro.data.assembler import erp_dataset
from appro.engine import run_mrp
from appro.engine.models import EngineParams
from appro.services.excel_service import SIM_FIRST_COL, SIM_HEADER_ROWS, block_layout, simulation_workbook

SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")


def _recalculate(content: bytes, tmp_path) -> dict:
    src = tmp_path / "simu.xlsx"
    src.write_bytes(content)
    profile = tmp_path / "profile"
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin"}
    r = subprocess.run([SOFFICE, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--calc",
                        "--convert-to", "xlsx", "--outdir", str(tmp_path / "out"), str(src)],
                       capture_output=True, text=True, timeout=300, env=env)
    out = tmp_path / "out" / "simu.xlsx"
    if not out.exists():
        pytest.skip(f"LibreOffice could not convert the workbook: {r.stdout} {r.stderr}")
    return load_workbook(out, data_only=True)


def _rows(ar) -> dict[str, int]:
    """Offset of every article-level row of the block (stocks, shortage, target, coverage)."""
    return {b.key: off for off, b in enumerate(block_layout(ar)) if b.lane is None}


@pytest.mark.skipif(SOFFICE is None, reason="LibreOffice not installed")
@pytest.mark.parametrize("policy", ["backlog", "lost"])
def test_workbook_formulas_match_engine(seed_source, tmp_path, policy):
    ds = erp_dataset(seed_source, planner="QUENTIN")
    res = run_mrp(ds, EngineParams(as_of=dt.date(2026, 9, 19), horizon_days=45, shortage_policy=policy))
    ids = ["P-00001046", "P-00005775", "P-00003751", "P-00003759"]     # P-00005775 has two supplier lanes
    wb = _recalculate(simulation_workbook(res, ids, "day"), tmp_path)
    ws = wb["SIMULATION"]
    ncols = ws.max_column - SIM_FIRST_COL + 1
    checked = 0
    top = SIM_HEADER_ROWS + 1
    for aid in ids:
        ar = res.articles[aid]
        rows = _rows(ar)
        i0 = ar.dates.index(res.as_of)
        for key, series in (("stock_erp", ar.stock_erp_net), ("stock_plan", ar.stock_plan_net),
                            ("shortage_plan", ar.shortage_plan), ("target", ar.target_stock), ("coverage", ar.coverage_plan)):
            for j in range(ncols):
                got = ws.cell(top + rows[key], SIM_FIRST_COL + j).value
                assert got is not None, (aid, key, j)
                assert abs(float(got) - float(series[i0 + j])) < 0.01, (aid, key, ar.dates[i0 + j], got, series[i0 + j])
                checked += 1
        top += len(block_layout(ar))
    assert checked == ncols * 5 * len(ids)


@pytest.mark.skipif(SOFFICE is None, reason="LibreOffice not installed")
def test_workbook_reacts_to_plan_and_adjustment_edits(seed_source, tmp_path):
    """Editing the Plan row moves the plan scenario only ; an adjustment moves both."""
    ds = erp_dataset(seed_source, planner="QUENTIN")
    res = run_mrp(ds, EngineParams(as_of=dt.date(2026, 9, 19), horizon_days=30))
    aid = "P-00001046"
    wb = load_workbook(io.BytesIO(simulation_workbook(res, [aid], "day")))
    ws = wb["SIMULATION"]
    ar = res.articles[aid]
    layout = block_layout(ar)
    rows = {b.key: SIM_HEADER_ROWS + 1 + off for off, b in enumerate(layout)}
    i0 = ar.dates.index(res.as_of)
    d_order = dt.date(2026, 9, 30)   # a firm order of 1600 is expected that day
    j = ar.dates.index(d_order) - i0
    assert ws.cell(rows["orders_firm"], SIM_FIRST_COL + j).value == 1600 and ws.cell(rows["plan"], SIM_FIRST_COL + j).value == 1600
    ws.cell(rows["plan"], SIM_FIRST_COL + j, 0)                    # nothing on 30/09 …
    ws.cell(rows["plan"], SIM_FIRST_COL + j + 6, 1600)             # … 1600 on 06/10
    ws.cell(rows["adjustments"], SIM_FIRST_COL + 3, -50)           # 22/09
    buf = io.BytesIO()
    wb.save(buf)
    calc = _recalculate(buf.getvalue(), tmp_path)["SIMULATION"]
    got_plan = calc.cell(rows["stock_plan"], SIM_FIRST_COL + j).value
    assert abs(float(got_plan) - (ar.stock_plan_net[i0 + j] - 50 - 1600)) < 0.01
    j2 = j + 6
    assert abs(float(calc.cell(rows["stock_plan"], SIM_FIRST_COL + j2).value) - (ar.stock_plan_net[i0 + j2] - 50)) < 0.01
    assert abs(float(calc.cell(rows["stock_erp"], SIM_FIRST_COL + j).value) - (ar.stock_erp_net[i0 + j] - 50)) < 0.01
