"""Supply flows of one article, per supplier **lane**: the ERP scenario (firm orders as is), the
plan scenario (the planner's plan cells, else the ERP), the receipts and the backlog.

Rules (docs/regles_metier.md § 3):

* the lanes are the **active links** of the article (priority order) ; the ERP flows of any other
  supplier are left out of the table (status views and search still show them) and summarised in an
  ``UNLINKED_FLOWS`` alert ; a supplier holding typed plan cells keeps a lane « hors lien » ;
* an ERP **order** is a delivery slot ``supplier|article|date|firm`` ; the ERP never moves nor
  cancels a slot and its remaining quantity is unreliable once the date is past ;
* the **ERP scenario** counts the open firm orders dated on or after the reference day, except the
  days the planner **ignored** (a click on the Ferme cell: ``order_ignored`` flag) ;
* the **plan scenario** counts, for each supplier and day, the typed plan cell when there is one,
  else the firm ERP quantity of the day (ignored days excluded).  A cell dated before the
  reference day has expired ;
* on the **reference day** nothing links a receipt to an order: the quantity still expected from
  the firm orders of a supplier is ``max(0, open − receipts of the day)`` (a typed cell is taken as
  is, the planner knows) ;
* the **backlog** of a supplier is the Σ ERP remaining quantity of its firm orders dated before the
  reference day (the ERP decrements the remaining quantity when the arrival journal is validated) ;
  an order the planner ignored is out of the projection but still late : it stays in the backlog ;
* only the receipts of an **open order** (``CO-…``) or of a **closed order declared** in the Référentiel
  (``ref_closed_orders``) enter the Reçu row and the stock ; the others stay in the status views ;
* a **receipt** is a validated arrival journal (status « Reçu », or « Comptabilisé » once the
  acknowledgement of receipt is validated) ; an arrival journal not validated yet (« En transit ») is
  shown in the Reçu row like an announced DESADV and never counted.

For display the Ferme row also carries, per day, the **ordered** and **ERP remaining** quantities
of the firm orders whatever their date (``orders_firm_ordered`` / ``orders_firm_open``): the cell
shows the ordered quantity, or ``remaining / ordered`` when a partial delivery took place.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .demand import DayIndex
from .models import (
    CellFlag,
    DesadvInfo,
    DesadvLine,
    EngineParams,
    Lane,
    OrderInfo,
    OrderLine,
    PlanCell,
    Receipt,
    ReceiptInfo,
    SupplierLink,
)

EPS = 1e-9


@dataclass
class _LaneAcc:
    supplier_id: str | None
    n: int
    orders_firm: np.ndarray = field(init=False)
    orders_firm_hist: np.ndarray = field(init=False)
    orders_forecast: np.ndarray = field(init=False)
    receipts: np.ndarray = field(init=False)
    plan: np.ndarray = field(init=False)
    plan_typed: np.ndarray = field(init=False)
    ordered: np.ndarray = field(init=False)
    open: np.ndarray = field(init=False)
    ignored: np.ndarray = field(init=False)
    desadv_open: np.ndarray = field(init=False)
    desadv_ko: np.ndarray = field(init=False)
    receipts_ko: np.ndarray = field(init=False)
    receipts_booked: np.ndarray = field(init=False)
    backlog_ordered: float = 0.0
    backlog_received: float = 0.0
    backlog_open: float = 0.0
    orders: list[OrderInfo] = field(default_factory=list)
    desadv: list[DesadvInfo] = field(default_factory=list)
    receipt_lines: list[ReceiptInfo] = field(default_factory=list)

    def __post_init__(self) -> None:
        n = self.n
        self.orders_firm, self.orders_firm_hist, self.orders_forecast = np.zeros(n), np.zeros(n), np.zeros(n)
        self.receipts, self.plan, self.ordered, self.open = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
        self.plan_typed, self.ignored = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
        self.desadv_open = np.zeros(n)
        self.desadv_ko, self.receipts_ko = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
        self.receipts_booked = np.zeros(n, dtype=bool)


def lane_ids(links: list[SupplierLink], cells: list[PlanCell], as_of: dt.date) -> list[str | None]:
    """Suppliers shown as lanes : the **active links** (priority order) and nothing else from the ERP.
    A supplier that still carries typed plan cells dated on/after the reference day keeps a lane
    (« hors lien » : the planner's own entries never vanish), so he can clear or move them.  The ERP
    orders, receipts and despatch advices of any other supplier stay out of the table (they remain in
    the status views and in the search)."""
    ids: list[str | None] = [l.supplier_id for l in sorted(links, key=lambda l: (l.priority, l.supplier_id)) if l.active]
    typed = {c.supplier_id for c in cells if c.date >= as_of and c.qty > EPS}
    for sid in sorted((s for s in typed if s and s not in ids)):
        ids.append(sid)
    if None in typed and not ids:
        ids.append(None)
    return ids


@dataclass
class UnlinkedFlows:
    """ERP flows of one supplier without an active link, left out of the supply table of the article."""

    supplier_id: str | None
    name: str
    orders_open: int = 0        # firm orders dated on/after the reference day still open in the ERP
    open_qty: float = 0.0
    orders_late: int = 0        # firm orders dated before the reference day still open (late)
    late_qty: float = 0.0
    receipts: int = 0           # validated receipts inside the window
    desadv: int = 0             # announced despatch advices


def unlinked_flows(lane_ids_: list[str | None], orders: list[OrderLine], receipts: list[Receipt],
                   desadv: list[DesadvLine] | None, supplier_names: dict[str, str], as_of: dt.date) -> list[UnlinkedFlows]:
    """What the table does not show : the ERP flows whose supplier has no lane."""
    shown = set(lane_ids_)
    if lane_ids_:
        shown.add(None)          # anonymous flows ride the first lane
    out: dict[str | None, UnlinkedFlows] = {}

    def of(sid: str | None) -> UnlinkedFlows:
        if sid not in out:
            out[sid] = UnlinkedFlows(sid, supplier_names.get(sid or "", sid or "sans fournisseur"))
        return out[sid]

    for o in orders:
        if o.supplier_id in shown or o.order_type.value != "FIRM" or not o.qty_open or o.qty_open <= EPS:
            continue
        u = of(o.supplier_id)
        if o.expected_date < as_of:
            u.orders_late += 1
            u.late_qty += float(o.qty_open)
        else:
            u.orders_open += 1
            u.open_qty += float(o.qty_open)
    for r in receipts:
        if r.supplier_id not in shown and r.qty and r.counted and not r.in_transit:
            of(r.supplier_id).receipts += 1
    for d in desadv or ():
        if d.supplier_id not in shown and d.state.strip().lower() != "annulé":
            of(d.supplier_id).desadv += 1
    return [out[k] for k in sorted(out, key=lambda x: x or "")]


def build_lanes(links: list[SupplierLink], orders: list[OrderLine], receipts: list[Receipt], cells: list[PlanCell],
                supplier_names: dict[str, str], index: DayIndex, as_of: dt.date, params: EngineParams,
                flags: list[CellFlag] | None = None, desadv: list[DesadvLine] | None = None) -> list[Lane]:
    n = index.n
    ids = lane_ids(links, cells, as_of)
    acc = {sid: _LaneAcc(sid, n) for sid in ids}
    linked = {l.supplier_id for l in links if l.active}

    first = next(iter(acc.values()), None)

    def lane_of(sid: str | None) -> _LaneAcc | None:
        """The lane of a supplier ; ``None`` when he has no lane : his flows stay out of the table.  A
        flow without any supplier cannot belong to « another supplier » : it rides the first lane."""
        return acc.get(sid) or (first if not sid else None)

    def add(arr: np.ndarray, day: dt.date, qty: float) -> None:
        i = index.offset(day)
        if i is not None:
            arr[i] += qty

    # ---- ignored firm days (planner clicks), on/after the reference day only
    for f in flags or ():
        if f.kind == "order_ignored" and f.date >= as_of:
            i = index.offset(f.date)
            ln = lane_of(f.supplier_id)
            if i is not None and ln is not None:
                ln.ignored[i] = True

    # ---- receipts : the validated arrival journals only (« Reçu », « Comptabilisé ») ; a journal not
    # validated yet (« En transit ») is on its way : shown, never counted
    # only the receipts that count (open orders, declared closed orders) reach the Reçu row and the stock
    posted = [r for r in receipts if r.qty and r.counted and not r.in_transit]
    transit = [r for r in receipts if r.qty and r.counted and r.in_transit]
    posted = [r for r in posted if lane_of(r.supplier_id) is not None]
    transit = [r for r in transit if lane_of(r.supplier_id) is not None]
    for r in posted:
        add(lane_of(r.supplier_id).receipts, r.receipt_date, r.qty)
    # ---- ERP orders
    received_today: dict[str | None, float] = defaultdict(float)
    for r in posted:
        if r.receipt_date == as_of:
            received_today[lane_of(r.supplier_id).supplier_id] += r.qty
    for o in sorted(orders, key=lambda o: (o.expected_date, o.order_id)):
        ln = lane_of(o.supplier_id)
        if ln is None:
            continue
        typ = o.order_type.value
        firm = typ == "FIRM"
        day = o.expected_date
        i = index.offset(day)
        ignored = bool(firm and i is not None and ln.ignored[i])
        ln.orders.append(OrderInfo(o.order_id, o.supplier_id, typ, day, float(o.qty_ordered), float(o.qty_open or 0.0),
                                   o.ref, ignored))
        if firm:
            add(ln.ordered, day, float(o.qty_ordered))
            add(ln.open, day, float(o.qty_open or 0.0))
            if day < as_of:
                add(ln.orders_firm_hist, day, float(o.qty_ordered))
                if o.qty_open and o.qty_open > EPS:      # late : its ERP remaining quantity is the backlog
                    ln.backlog_ordered += float(o.qty_ordered)
                    ln.backlog_received += float(o.qty_ordered) - float(o.qty_open)
                    ln.backlog_open += float(o.qty_open)
            elif o.qty_open and o.qty_open > EPS and not ignored:
                add(ln.orders_firm, day, float(o.qty_open))
        elif o.qty_open and o.qty_open > EPS:
            add(ln.orders_forecast, day, float(o.qty_open))
    # reference day: receipts of the day already cover part of the firm orders of the day
    i0 = index.offset(as_of)
    if i0 is not None:
        for ln in acc.values():
            ln.orders_firm[i0] = max(0.0, ln.orders_firm[i0] - received_today.get(ln.supplier_id, 0.0))
    # ---- in transit : the arrival journals not validated yet and the announced DESADV, matched to the
    # receipts by delivery note (BL).  Green dot = the BL is announced by a processed DESADV, red otherwise.
    hidden_days = {(f.supplier_id or None, f.date) for f in (flags or ()) if f.kind == "desadv_hidden"}
    live = [d for d in (desadv or ()) if d.state.strip().lower() != "annulé" and lane_of(d.supplier_id) is not None]
    processed_bls = {d.packing_slip.strip() for d in live if d.processed and d.packing_slip.strip()}
    received_bls = {bl for r in posted for bl in r.packing_slips}
    transit_bls = {bl for r in transit for bl in r.packing_slips}
    for r in sorted(transit, key=lambda r: (r.receipt_date, r.receipt_id)):
        ln = lane_of(r.supplier_id)
        bl = (r.packing_slip or "").strip()
        processed = any(x in processed_bls for x in r.packing_slips)
        hidden = (ln.supplier_id, r.receipt_date) in hidden_days
        ln.desadv.append(DesadvInfo(r.receipt_id, bl, r.receipt_date, float(r.qty), "Journal d'arrivée non validé", "",
                                    processed, False, hidden, r.ref or "", "", False, "journal"))
        i = index.offset(r.receipt_date)
        if hidden or i is None:
            continue
        ln.desadv_open[i] += float(r.qty)
        if not processed:
            ln.desadv_ko[i] = True
    for d in live:
        bl = d.packing_slip.strip()
        ln = lane_of(d.supplier_id)
        received = bl in received_bls
        journaled = bool(bl) and bl in transit_bls           # shown through its arrival journal
        hidden = (ln.supplier_id, d.issue_date) in hidden_days
        ln.desadv.append(DesadvInfo(d.desadv_id, bl, d.issue_date, float(d.qty), d.state, d.final_processing,
                                    d.processed, received, hidden, d.purch_id, d.stock_trans_id, journaled))
        if received or journaled or hidden:
            continue
        i = index.offset(d.issue_date)
        if i is not None:
            ln.desadv_open[i] += float(d.qty)
            if not d.processed:
                ln.desadv_ko[i] = True
    unbooked = {sid: np.zeros(n, dtype=bool) for sid in acc}
    for r in sorted(posted, key=lambda r: (r.receipt_date, r.receipt_id)):
        i = index.offset(r.receipt_date)
        if i is None:                                         # nothing exists before the point zero
            continue
        bl = (r.packing_slip or "").strip()
        # green when the BL is announced by a processed DESADV ; a manual receipt (no BL, or a BL unknown to
        # the processed DESADV) is red ; the acknowledgement of receipt gives the green background
        processed = any(x in processed_bls for x in r.packing_slips)
        ln = lane_of(r.supplier_id)
        ln.receipt_lines.append(ReceiptInfo(r.receipt_id, r.receipt_date, float(r.qty), bl, r.ref or "", processed, r.status, r.booked,
                                            order_kind=r.order_kind))
        if not processed and r.order_kind != "po":          # a closed order has no DESADV : never red for that
            ln.receipts_ko[i] = True
        ln.receipts_booked[i] = True
        if not r.booked:
            unbooked[ln.supplier_id][i] = True
    for sid, ln in acc.items():
        ln.receipts_booked &= ~unbooked[sid]
    # ---- plan: typed cells, else the ERP
    for ln in acc.values():
        ln.plan[:] = ln.orders_firm
    for c in cells:
        if c.date < as_of:
            continue
        i = index.offset(c.date)
        if i is None:
            continue
        ln = lane_of(c.supplier_id)
        if ln is None:
            continue
        ln.plan[i] = max(0.0, float(c.qty))
        ln.plan_typed[i] = True
    return [Lane(supplier_id=ln.supplier_id, name=supplier_names.get(ln.supplier_id or "", ln.supplier_id or ""),
                 orders_firm=ln.orders_firm, orders_firm_hist=ln.orders_firm_hist, orders_forecast=ln.orders_forecast,
                 receipts=ln.receipts, plan=ln.plan, supply_proposed=np.zeros(n), plan_typed=ln.plan_typed,
                 desadv_open=ln.desadv_open, desadv_ko=ln.desadv_ko, receipts_ko=ln.receipts_ko, receipts_booked=ln.receipts_booked,
                 desadv=ln.desadv, receipt_lines=ln.receipt_lines,
                 orders_firm_ordered=ln.ordered, orders_firm_open=ln.open, orders_ignored=ln.ignored,
                 backlog_ordered=ln.backlog_ordered, backlog_received=ln.backlog_received,
                 backlog_qty=ln.backlog_open, orders=ln.orders, linked=ln.supplier_id in linked)
            for ln in acc.values()]


def lane_totals(lanes: list[Lane], key: str, n: int = 0) -> np.ndarray:
    """Σ of one series over the lanes ; ``n`` sizes the result when there is no lane at all."""
    if not lanes:
        return np.zeros(n)
    out = np.zeros_like(getattr(lanes[0], key), dtype=float)
    for l in lanes:
        out += getattr(l, key)
    return out


def blocked_days(flags: list[CellFlag] | None, article_id: str) -> dict[str | None, set[dt.date]]:
    """Days where no CBN proposal may be placed, per supplier : each refused proposal blocks its own
    day only (the need moves to the next delivery day of the supplier).  A refusal without supplier
    (key ``None``) applies to every supplier."""
    out: dict[str | None, set[dt.date]] = {}
    for f in flags or ():
        if f.kind == "proposal_refused" and f.article_id == article_id:
            out.setdefault(f.supplier_id or None, set()).add(f.date)
    return out
