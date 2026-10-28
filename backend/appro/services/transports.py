"""Transports announced to the suppliers (« Planning de livraisons »).

A **transport** is one truck expected from one supplier on one day, at one time, on one dock, carrying
some of the supplier's **firm ERP orders** of that day.  Its identifier is ``EM`` + 6 digits, drawn from a
shared counter (never reused).  Creating transports produces, in one go :

* the rows (``app_transports`` / ``app_transport_orders``), journalised (not undoable : the CSV and the
  e-mail have left the application) ;
* **one CSV line per transport** for the external transport tool (ATR) : ``;`` separated, no header,
  ``id ; supplier name ; order numbers ; dd/mm/yyyy ; hh:mm:ss ; ; R ; ; ; dock`` (the flow is always ``R``) ;
* the e-mail to the supplier's **transport contacts** (``ref_suppliers.transport_contact``) : recipients,
  subject and body, opened by the planner in Outlook (``mailto:``) and sent by hand.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data.store import AppCounter, AppTransport, AppTransportOrder, audit
from ..engine.calendar import WorkCalendar
from ..engine.models import Dataset, OrderLine, Supplier

COUNTER = "transport"
DOCKS = ("B15", "B06", "B2NORD")
FLOW = "R"
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# ----------------------------------------------------------------------------- identifiers
def next_transport_id(session: Session) -> str:
    """``EM000001``, ``EM000002``… : the counter row is locked (PostgreSQL ``FOR UPDATE``) until the commit."""
    row = session.execute(select(AppCounter).where(AppCounter.name == COUNTER).with_for_update()).scalar_one_or_none()
    if row is None:
        row = AppCounter(name=COUNTER, value=0)
        session.add(row)
        session.flush()
    row.value = int(row.value or 0) + 1
    session.flush()
    return f"EM{row.value:06d}"


# ----------------------------------------------------------------------------- planning
def working_days(start: dt.date, weeks: int, holidays: list[dt.date]) -> list[dt.date]:
    cal = WorkCalendar.from_spec(holidays=holidays)
    end = start + dt.timedelta(days=7 * weeks - 1)
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1) if cal.is_working_day(start + dt.timedelta(days=i))]


def transports_between(session: Session, start: dt.date, end: dt.date, supplier_id: str | None = None,
                       include_cancelled: bool = False) -> list[AppTransport]:
    q = select(AppTransport).where(AppTransport.date >= start, AppTransport.date <= end)
    if supplier_id:
        q = q.where(AppTransport.supplier_id == supplier_id)
    if not include_cancelled:
        q = q.where(AppTransport.status == "planned")
    return list(session.scalars(q.order_by(AppTransport.date, AppTransport.time, AppTransport.transport_id)).all())


def transport_out(t: AppTransport, names: dict[str, str]) -> dict[str, Any]:
    return {"transport_id": t.transport_id, "supplier_id": t.supplier_id, "supplier_name": names.get(t.supplier_id, ""),
            "date": t.date, "time": t.time, "dock": t.dock, "note": t.note or "", "status": t.status,
            "created_by": t.created_by, "created_at": t.created_at,
            "orders": [{"order_id": o.order_id, "article_id": o.article_id, "ref": o.ref or "", "expected_date": o.expected_date, "qty": o.qty}
                       for o in t.orders]}


def planning(ds: Dataset, session: Session, start: dt.date, weeks: int, holidays: list[dt.date], as_of: dt.date) -> dict[str, Any]:
    """Suppliers × working days : the firm ERP orders of the window (active articles of the dataset), with
    the transports already created for them."""
    days = working_days(start, weeks, holidays)
    end = days[-1] if days else start
    arts = {a.article_id: a for a in ds.articles if a.active}
    ignored = {(f.article_id, f.supplier_id or None, f.date) for f in ds.flags if f.kind == "order_ignored"}
    transports = transports_between(session, start, end)
    by_order: dict[str, list[str]] = {}
    for t in transports:
        for o in t.orders:
            by_order.setdefault(o.order_id, []).append(t.transport_id)
    sups = {s.supplier_id: s for s in ds.suppliers}
    rows: dict[str, dict[str, Any]] = {}
    for o in ds.orders:
        if o.order_type.value != "FIRM" or not o.supplier_id or o.expected_date < start or o.expected_date > end:
            continue
        a = arts.get(o.article_id)
        if a is None:
            continue
        s = sups.get(o.supplier_id)
        row = rows.setdefault(o.supplier_id, {"supplier_id": o.supplier_id, "name": s.name if s else "", "transport_contact": s.transport_contact if s else "",
                                               "emails": s.transport_emails if s else [], "orders": []})
        row["orders"].append({"order_id": o.order_id, "article_id": o.article_id, "designation": a.designation, "unit": a.unit, "ref": o.ref or "",
                              "expected_date": o.expected_date, "qty_ordered": float(o.qty_ordered), "qty_open": float(o.qty_open or 0.0),
                              "ignored": (o.article_id, o.supplier_id, o.expected_date) in ignored, "transports": by_order.get(o.order_id, [])})
    for r in rows.values():
        r["orders"].sort(key=lambda x: (x["expected_date"], x["article_id"], x["ref"]))
    names = {sid: s.name for sid, s in sups.items()}
    return {"as_of": as_of, "start": start, "end": end, "days": days, "docks": list(DOCKS),
            "suppliers": sorted(rows.values(), key=lambda r: (r["name"].upper(), r["supplier_id"])),
            "transports": [transport_out(t, names) for t in transports]}


# ----------------------------------------------------------------------------- creation
class TransportError(ValueError):
    pass


def create_transports(session: Session, user: str, ds: Dataset, supplier_id: str, order_ids: list[str],
                      slots: list[dict[str, str]], note: str = "") -> list[AppTransport]:
    """One transport per slot (date, time, dock), every slot carrying the same firm orders of ``supplier_id``.
    Does not commit."""
    orders: dict[str, OrderLine] = {o.order_id: o for o in ds.orders if o.order_type.value == "FIRM" and o.supplier_id == supplier_id}
    missing = [oid for oid in order_ids if oid not in orders]
    if missing:
        raise TransportError(f"Commande(s) inconnue(s) pour le fournisseur {supplier_id} : {', '.join(missing)}")
    if not slots:
        raise TransportError("Au moins un transport (date, heure, quai) est nécessaire")
    created: list[AppTransport] = []
    for sl in slots:
        time, dock = str(sl.get("time") or "").strip(), str(sl.get("dock") or "").strip()
        if not TIME_RE.match(time):
            raise TransportError(f"Heure invalide : « {time} » (attendu HH:MM)")
        if dock not in DOCKS:
            raise TransportError(f"Quai inconnu : « {dock} » (attendu {', '.join(DOCKS)})")
        day = sl["date"] if isinstance(sl["date"], dt.date) else dt.date.fromisoformat(str(sl["date"]))
        t = AppTransport(transport_id=next_transport_id(session), supplier_id=supplier_id, date=day, time=time, dock=dock,
                         note=(note or "").strip()[:500], status="planned", created_by=user)
        for oid in dict.fromkeys(order_ids):
            o = orders[oid]
            t.orders.append(AppTransportOrder(order_id=oid, article_id=o.article_id, ref=o.ref or "", expected_date=o.expected_date,
                                              qty=float(o.qty_open if o.qty_open is not None else o.qty_ordered)))
        session.add(t)
        session.flush()
        audit(session, user, "create", "transport", t.transport_id, None,
              {"supplier_id": supplier_id, "date": day.isoformat(), "time": time, "dock": dock, "orders": list(dict.fromkeys(order_ids))})
        created.append(t)
    return created


def cancel_transport(session: Session, user: str, t: AppTransport) -> None:
    t.status = "cancelled"
    audit(session, user, "cancel", "transport", t.transport_id, None, {"supplier_id": t.supplier_id, "date": t.date.isoformat()})
    session.flush()


# ----------------------------------------------------------------------------- CSV (ATR) and e-mail
def _refs(t: AppTransport) -> list[str]:
    out: list[str] = []
    for o in t.orders:
        for ref in re.split(r"[;,\s]+", o.ref or ""):
            if ref and ref not in out:
                out.append(ref)
    return out


def atr_csv(transports: list[AppTransport], names: dict[str, str]) -> str:
    """One line per transport, ``;`` separated, no header, CRLF (the external tool runs on Windows) :
    ``EM000001;<supplier name>;<order numbers>;03/11/2026;14:00:00;;R;;;B15``."""
    lines = []
    for t in transports:
        cells = [t.transport_id, names.get(t.supplier_id, t.supplier_id), " ".join(_refs(t)), f"{t.date:%d/%m/%Y}", f"{t.time}:00", "", FLOW, "", "", t.dock]
        lines.append(";".join(c.replace(";", " ") for c in cells))
    return "\r\n".join(lines) + "\r\n"


def csv_filename(transports: list[AppTransport]) -> str:
    ids = [t.transport_id for t in transports]
    return f"ATR_{ids[0]}.csv" if len(ids) == 1 else f"ATR_{ids[0]}-{ids[-1]}.csv"


def _qty(v: float, unit: str) -> str:
    s = f"{v:,.3f}".rstrip("0").rstrip(".").replace(",", " ")
    return f"{s} {unit}".strip()


def mail_parts(transports: list[AppTransport], supplier: Supplier | None, articles: dict[str, Any], user: str) -> dict[str, Any]:
    """Recipients (transport contacts of the supplier), subject and body of the notice, in French then
    English : identifiers, dates, times and docks of the transports, then the orders they carry."""
    name = supplier.name if supplier else (transports[0].supplier_id if transports else "")
    ids = ", ".join(t.transport_id for t in transports)
    refs: list[str] = []
    for t in transports:
        for r in _refs(t):
            if r not in refs:
                refs.append(r)
    first = transports[0]
    when = f"{first.date:%d/%m/%Y} {first.time}" if len(transports) == 1 else f"{first.date:%d/%m/%Y}" + ("" if all(t.date == first.date for t in transports) else " …")
    docks = sorted({t.dock for t in transports})
    subject = f"Transport {ids} – {name} – livraison {when} – quai {' / '.join(docks)}" + (f" – commande(s) {', '.join(refs)}" if refs else "")
    plural = len(transports) > 1
    lines = ["Bonjour,", "",
             f"Merci de livrer les commandes ci-dessous selon {'les transports suivants' if plural else 'le transport suivant'} ; "
             f"{'les identifiants de transport sont' if plural else 'l’identifiant de transport est'} à rappeler sur les documents de livraison et au chauffeur :", ""]
    for t in transports:
        lines.append(f"  • {t.transport_id} – {t.date:%d/%m/%Y} à {t.time} – quai {t.dock}")
    lines += ["", "Commandes concernées :"]
    for o in first.orders:
        a = articles.get(o.article_id)
        desig = f" {a.designation}" if a is not None and a.designation else ""
        unit = a.unit if a is not None else ""
        lines.append(f"  • {o.ref or o.order_id} – {o.article_id}{desig} – {_qty(o.qty, unit)}" + (f" – prévue le {o.expected_date:%d/%m/%Y}" if o.expected_date else ""))
    if first.note:
        lines += ["", first.note]
    lines += ["", "Cordialement,", user, "", "---", "",
              f"Please deliver the orders below according to the transport{'s' if plural else ''} above ; the transport ID{'s' if plural else ''} must appear on the delivery documents and be known to the driver."]
    return {"to": supplier.transport_emails if supplier else [], "subject": subject, "body": "\n".join(lines)}
