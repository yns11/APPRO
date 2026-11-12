"""Benchmark of the backend on a local dataset : engine computation, API answers (size and latency), cache footprint.

    python scripts/bench.py                       # the seed, QUENTIN's perimeter, 20 iterations
    python scripts/bench.py --runs 50 --planner "" # every article, 50 iterations
    APPRO_SEED_DIR=/path/to/extract python scripts/bench.py   # a realistic extraction (same CSV layout as data/seed)

Prints p50 / p95 of : the engine alone (``run_mrp``), the cockpit, the supply table page, one article projection,
the flows, the Excel export, and a write (plan cell) followed by the recomputation it triggers ; the size of each
answer ; the memory of one cached result and of the process (``tracemalloc``).  No threshold is enforced : the
numbers are meant to be compared from one version to the next (docs/performance.md)."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from appro.api.main import create_app  # noqa: E402
from appro.config import Settings  # noqa: E402
from appro.data.assembler import erp_dataset  # noqa: E402
from appro.data.sources import LocalCsvSource  # noqa: E402
from appro.data.store import init_store  # noqa: E402
from appro.engine import run_mrp  # noqa: E402
from appro.engine.models import EngineParams  # noqa: E402
from appro.services.context import AppContext, bootstrap_reference, estimate_size, set_context  # noqa: E402


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def timed(fn, runs: int) -> tuple[list[float], object]:
    out, last = [], None
    for _ in range(runs):
        t0 = time.perf_counter()
        last = fn()
        out.append(1000 * (time.perf_counter() - t0))
    return out, last


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--planner", default="QUENTIN")
    ap.add_argument("--as-of", default="2026-09-25")
    ap.add_argument("--seed", default=os.environ.get("APPRO_SEED_DIR", str(ROOT / "data" / "seed")))
    args = ap.parse_args()
    seed = Path(args.seed)
    planner = args.planner or None
    as_of = dt.date.fromisoformat(args.as_of)
    tracemalloc.start()
    source = LocalCsvSource(seed)
    db = ROOT / "data" / "local" / "bench.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    engine = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
    factory = init_store(engine)
    bootstrap_reference(factory, seed)
    settings = Settings(data_source="local", seed_dir=seed, as_of=as_of, static_dir=seed / "no-such-dir")
    ctx = AppContext(settings=settings, source=source, session_factory=factory)
    set_context(ctx)
    client = TestClient(create_app(), headers={"x-forwarded-email": "bench@example.com"})
    ds = erp_dataset(source, planner=planner)
    n_art = len(ds.articles)
    print(f"dataset : {n_art} articles, {len(ds.orders)} créneaux, {len(ds.receipts)} réceptions, {len(ds.consumption)} consommations · as_of {as_of} · {args.runs} itérations")

    rows: list[tuple[str, list[float], int]] = []
    eng, res = timed(lambda: run_mrp(ds, EngineParams(as_of=as_of)), args.runs)
    rows.append(("moteur run_mrp (sans cache)", eng, estimate_size(res)))
    first = client.get("/api/articles/" + ds.articles[0].article_id + "/projection").json()   # warms the cache
    aid = first["article"]["article_id"]

    def call(path: str, **params):
        r = client.get(path, params={k: v for k, v in params.items() if v is not None})
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
        return len(r.content)

    for label, path, params in (
        ("cockpit (cache chaud)", "/api/cockpit", {"planner": planner}),
        ("tableau d'appro, page de 20", "/api/grid", {"planner": planner, "page": 1, "page_size": 20}),
        ("fiche article (projection)", f"/api/articles/{aid}/projection", {}),
        ("flux du jour", "/api/flows", {"planner": planner}),
        ("file d'exceptions", "/api/exceptions", {"planner": planner}),
        ("OTD fournisseurs (12 mois)", "/api/performance/otd", {"planner": planner}),
        ("export Excel simulation", "/api/exports/simulation.xlsx", {"planner": planner}),
    ):
        ms, size = timed(lambda p=path, q=params: call(p, **q), args.runs)
        rows.append((label, ms, int(size)))
    # a write (plan cell) and the recomputation it forces
    day = (as_of + dt.timedelta(days=3)).isoformat()
    i = [0]

    def write_then_read():
        i[0] += 1
        r = client.put("/api/entries/plan", json={"article_id": aid, "supplier_id": None, "date": day, "expression": str(100 + i[0])})
        assert r.status_code == 200, r.text
        return call("/api/cockpit", planner=planner)

    ms, size = timed(write_then_read, max(3, args.runs // 4))
    rows.append(("saisie + recalcul du tableau de bord", ms, int(size)))
    current, peak = tracemalloc.get_traced_memory()
    print(f"\n{'mesure':44} {'p50 ms':>9} {'p95 ms':>9} {'max ms':>9} {'taille':>10}")
    for label, ms, size in rows:
        print(f"{label:44} {statistics.median(ms):9.0f} {pct(ms, 0.95):9.0f} {max(ms):9.0f} {size / 1024:9.0f} k")
    stats = ctx.cache_stats()
    print(f"\ncache : {stats['entries']} résultats, {stats['bytes'] / 1e6:.1f} Mo (budget {stats['budget'] / 1e6:.0f} Mo) · mémoire Python {current / 1e6:.0f} Mo, pic {peak / 1e6:.0f} Mo")
    set_context(None)


if __name__ == "__main__":
    main()
