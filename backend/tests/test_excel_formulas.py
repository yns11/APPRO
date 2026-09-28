"""The live workbook must compute the same stocks as the engine.

The workbook is recalculated with LibreOffice (headless) when it is available (GitHub's
``ubuntu-latest`` runners ship it); otherwise the test is skipped.
"""
from __future__ import annotations

import datetime as dt
import shutil
import subprocess

import pytest
from openpyxl import load_workbook

from appro.data.assembler import erp_dataset
from appro.engine import run_mrp
from appro.engine.models import EngineParams
from appro.services.excel_service import BLOCK, ROW, SIM_FIRST_COL, SIM_HEADER_ROWS, simulation_workbook

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


@pytest.mark.skipif(SOFFICE is None, reason="LibreOffice not installed")
@pytest.mark.parametrize("policy", ["backlog", "lost"])
def test_workbook_formulas_match_engine(seed_source, tmp_path, policy):
    ds = erp_dataset(seed_source, planner="QUENTIN")
    res = run_mrp(ds, EngineParams(as_of=dt.date(2026, 9, 19), horizon_days=45, shortage_policy=policy))
    ids = list(res.articles)[:4]
    wb = _recalculate(simulation_workbook(res, ids, "day"), tmp_path)
    ws = wb["SIMULATION"]
    ncols = ws.max_column - SIM_FIRST_COL + 1
    checked = 0
    for bi, aid in enumerate(ids):
        ar = res.articles[aid]
        top = SIM_HEADER_ROWS + 1 + bi * len(BLOCK)
        i0 = ar.dates.index(res.as_of)
        for key, series in (("stock_erp", ar.stock_erp_net), ("stock_plan", ar.stock_plan_net),
                            ("shortage_plan", ar.shortage_plan), ("target", ar.target_stock), ("coverage", ar.coverage_plan)):
            for j in range(ncols):
                got = ws.cell(top + ROW[key], SIM_FIRST_COL + j).value
                assert got is not None, (aid, key, j)
                assert abs(float(got) - float(series[i0 + j])) < 0.01, (aid, key, ar.dates[i0 + j], got, series[i0 + j])
                checked += 1
    assert checked == ncols * 5 * len(ids)


@pytest.mark.skipif(SOFFICE is None, reason="LibreOffice not installed")
def test_workbook_reacts_to_entries(seed_source, tmp_path):
    """Typing in SAISIES and editing the PLAN sheet changes the stocks as the engine would."""
    ds = erp_dataset(seed_source, planner="QUENTIN")
    res = run_mrp(ds, EngineParams(as_of=dt.date(2026, 9, 19), horizon_days=30))
    aid = "P-00001046"
    wb = load_workbook(__import__("io").BytesIO(simulation_workbook(res, [aid], "day")))
    ws = wb["SAISIES"]
    for r, row in ((3, ["COMMANDE", aid, "S-000545", dt.date(2026, 9, 25), 1000]),
                   (4, ["AJUSTEMENT", aid, "", dt.date(2026, 9, 22), -50])):
        for c, v in enumerate(row, start=1):
            ws.cell(r, c, v)
    ar = res.articles[aid]
    i0 = ar.dates.index(res.as_of)
    top = SIM_HEADER_ROWS + 1
    d_order = dt.date(2026, 9, 30)   # a firm order of 1600 is expected that day
    # plan edit: the 30/09 order is delayed to 06/10 in the PLAN sheet → plan scenario only
    plan = wb["PLAN"]
    row = next(r for r in range(2, plan.max_row + 1) if plan.cell(r, 4).value == "PO-000016")
    assert str(plan.cell(row, 6).value)[:10] == d_order.isoformat() and plan.cell(row, 7).value == 1600
    plan.cell(row, 8, dt.date(2026, 10, 6))
    buf = __import__("io").BytesIO()
    wb.save(buf)
    calc = _recalculate(buf.getvalue(), tmp_path)["SIMULATION"]
    j = ar.dates.index(d_order) - i0
    got_plan = calc.cell(top + ROW["stock_plan"], SIM_FIRST_COL + j).value
    assert abs(float(got_plan) - (ar.stock_plan_net[i0 + j] + 1000 - 50 - 1600)) < 0.01
    assert abs(float(calc.cell(top + ROW["plan"], SIM_FIRST_COL + j).value) - 0) < 0.01
    assert abs(float(calc.cell(top + ROW["plan"], SIM_FIRST_COL + j + 6).value) - 1600) < 0.01
    j2 = ar.dates.index(dt.date(2026, 10, 6)) - i0
    assert abs(float(calc.cell(top + ROW["stock_plan"], SIM_FIRST_COL + j2).value) - (ar.stock_plan_net[i0 + j2] + 1000 - 50)) < 0.01
    assert abs(float(calc.cell(top + ROW["stock_erp"], SIM_FIRST_COL + j).value) - (ar.stock_erp_net[i0 + j] + 1000 - 50)) < 0.01
