"""Transports announced to the suppliers (« Planning de livraisons »).

A **transport** is one truck expected from one supplier on one day, at one time, on one or several docks,
carrying some of the supplier's **firm ERP orders** of that day.  Its identifier is ``EM`` + 6 digits, drawn
from a shared counter (never reused).  Creating transports produces :

* the rows (``app_transports`` / ``app_transport_orders``), journalised (not undoable : the CSV and the
  e-mail may already have left the application) ;
* the **CSV for the external transport tool (ATR)** : ``;`` separated, header
  ``ID TRANSPORT;FOURNISSEUR;TRANSPORTEUR;DATE;HEURE;CODE FOURNISSEUR;FLUX;CHAMP1;CHAMP2;QUAIS``, **one line per
  transport and per dock** (every other field repeated, the identifier included), flow always ``R`` ; the
  planner downloads it per transport, or the global one (every planned transport from today on, every
  supplier, every planner) to limit the imports into the tool ;
* the e-mail to the supplier's **transport contacts** (``ref_suppliers.transport_contact``) : recipients,
  subject and body, opened by the planner in Outlook (``mailto:``) and sent by hand.

**Automatic trucks** (``ref_suppliers.transport_auto``) : the application creates, completes, cancels and schedules
the trucks itself and prepares the weekly plan e-mail (services/transports_auto.py) ; this module keeps the manual
creation (MAF shuttle, suppliers in manual), the CSV and the per-truck e-mail.

**Transport modes** (``ref_suppliers.transport_mode``) :

* **Fournisseur** (default) : the supplier delivers ; his orders sit in the main planning ;
* **MAF Hambach** : the parts wait at the advanced supplier warehouse ; the orders sit in the « Planning MAF »
  tab, open to every planner whatever the perimeter, and one **shuttle** transport may carry the orders of several
  suppliers ; the CSV shows ``NAVETTE HAMBACH`` as supplier name and code ;
* **Messagerie** : parcel flow, no truck : the supplier is out of the planning and his firm orders are marked in
  the supply table as if a transport existed ;
* **a carrier name** (table ``ref_carriers``) : the carrier appears in the CSV column TRANSPORTEUR and the e-mail
  goes to the carrier's contacts.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data.store import AppCounter, AppTransport, AppTransportOrder, audit
from ..engine.calendar import WorkCalendar
from ..engine.models import Dataset, Supplier

COUNTER = "transport"
DOCKS = ("B15", "B6 EST", "B2 NORD", "B6 NORD")
MODE_SUPPLIER, MODE_MAF, MODE_PARCEL = "Fournisseur", "MAF Hambach", "Messagerie"
FIXED_MODES = (MODE_SUPPLIER, MODE_MAF, MODE_PARCEL)
MAF_ID = "MAF-HAMBACH"                 # supplier_id of a shuttle transport
MAF_LABEL = "NAVETTE HAMBACH"          # CSV supplier name and code of a shuttle
PARCEL_MARK = "Messagerie"             # what the grid shows instead of a transport identifier
FLOW = "R"
CSV_HEADER = ("ID TRANSPORT", "FOURNISSEUR", "TRANSPORTEUR", "DATE", "HEURE", "CODE FOURNISSEUR", "FLUX", "CHAMP1", "CHAMP2", "QUAIS")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def split_docks(text: str | None) -> list[str]:
    return [x.strip() for x in re.split(r"[;,]+", text or "") if x.strip()]


def split_emails(text: str | None) -> list[str]:
    return [x.strip().lower() for x in re.split(r"[;,\s]+", text or "") if x.strip()]


def mode_kind(mode: str | None) -> str:
    """``fournisseur`` | ``maf`` | ``messagerie`` | ``transporteur`` (any other non-empty value is a carrier name)."""
    m = (mode or "").strip()
    if not m or m.lower() == MODE_SUPPLIER.lower():
        return "fournisseur"
    if m.lower() == MODE_MAF.lower():
        return "maf"
    if m.lower() == MODE_PARCEL.lower():
        return "messagerie"
    return "transporteur"


def carriers_of(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """``carrier name → contact e-mails`` of the active carriers (``ref_carriers``)."""
    return {str(r.get("name") or "").strip(): split_emails(str(r.get("contacts") or "")) for r in rows if str(r.get("name") or "").strip() and bool(r.get("active", True))}


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


# ----------------------------------------------------------------------------- queries
def working_days(start: dt.date, weeks: int, holidays: list[dt.date], open_days: list[dt.date] | None = None) -> list[dt.date]:
    cal = WorkCalendar.from_spec(holidays=holidays, extra_open=open_days)
    end = start + dt.timedelta(days=7 * weeks - 1)
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1) if cal.is_working_day(start + dt.timedelta(days=i))]


def list_transports(session: Session, start: dt.date | None = None, end: dt.date | None = None, supplier_ids: list[str] | None = None,
                    include_cancelled: bool = False) -> list[AppTransport]:
    q = select(AppTransport)
    if start is not None:
        q = q.where(AppTransport.date >= start)
    if end is not None:
        q = q.where(AppTransport.date <= end)
    if supplier_ids is not None:
        q = q.where(AppTransport.supplier_id.in_(supplier_ids))
    if not include_cancelled:
        q = q.where(AppTransport.status == "planned")
    return list(session.scalars(q.order_by(AppTransport.date, AppTransport.time, AppTransport.transport_id)).all())


def transports_by_order(session: Session, ds: Dataset | None = None) -> dict[str, list[str]]:
    """``order_id → [transport ids]`` of the planned transports (the grid marks the firm order cells) ; with the
    dataset, the firm orders of the « Messagerie » suppliers carry the mark ``Messagerie`` (no truck to announce)."""
    out: dict[str, list[str]] = {}
    for t in list_transports(session):
        for o in t.orders:
            out.setdefault(o.order_id, []).append(t.transport_id)
    if ds is not None:
        parcel = {sp.supplier_id for sp in ds.suppliers if mode_kind(sp.transport_mode) == "messagerie"}
        if parcel:
            for o in ds.orders:
                if o.order_type.value == "FIRM" and o.supplier_id in parcel:
                    out.setdefault(o.order_id, []).append(PARCEL_MARK)
    return out


def transport_out(t: AppTransport, names: dict[str, str]) -> dict[str, Any]:
    return {"transport_id": t.transport_id, "supplier_id": t.supplier_id, "supplier_name": MAF_LABEL if t.supplier_id == MAF_ID else names.get(t.supplier_id, ""),
            "date": t.date, "time": t.time, "docks": split_docks(t.docks), "carrier": t.carrier or "", "note": t.note or "", "status": t.status,
            "mode": t.mode or "fournisseur", "created_by": t.created_by, "created_at": t.created_at,
            "origin": t.origin or "manuel", "pinned": bool(t.pinned), "check_reason": t.check_reason or "",
            "changed_since_sent": bool(t.changed_since_sent), "change_note": t.change_note or "",
            "orders": [{"order_id": o.order_id, "article_id": o.article_id, "supplier_id": o.supplier_id or t.supplier_id,
                        "supplier_name": names.get(o.supplier_id or t.supplier_id, ""), "ref": o.ref or "", "expected_date": o.expected_date, "qty": o.qty}
                       for o in t.orders]}


def planning(ds: Dataset, session: Session, start: dt.date, weeks: int, holidays: list[dt.date], as_of: dt.date,
             scope: str = "main", carriers: dict[str, list[str]] | None = None, plan_weekday: int = 4, slots: list[str] | None = None) -> dict[str, Any]:
    """Suppliers × working days : the firm ERP orders of the window (active articles of the dataset), with
    the transports already created for them.  Only the suppliers with an order in the window appear.
    ``scope`` : ``main`` = the suppliers who deliver themselves or through a carrier ; ``maf`` = the suppliers
    whose parts wait at the MAF Hambach (the shuttle planning).  « Messagerie » suppliers never appear."""
    days = working_days(start, weeks, sorted(set(holidays) | set(ds.holidays)), ds.open_days)
    end = days[-1] if days else start
    arts = {a.article_id: a for a in ds.articles if a.active}
    ignored = {(f.article_id, f.supplier_id or None, f.date) for f in ds.flags if f.kind == "order_ignored"}
    sups = {s.supplier_id: s for s in ds.suppliers}
    wanted = {"maf"} if scope == "maf" else {"fournisseur", "transporteur"}
    transports = [t for t in list_transports(session, start, end, include_cancelled=True) if (t.mode == "maf") == (scope == "maf")
                  and (t.status == "planned" or bool(t.changed_since_sent))]
    by_order: dict[str, list[str]] = {}
    for t in transports:
        if t.status != "planned":
            continue
        for o in t.orders:
            by_order.setdefault(o.order_id, []).append(t.transport_id)
    carriers = carriers or {}
    rows: dict[str, dict[str, Any]] = {}
    for o in ds.orders:
        if o.order_type.value != "FIRM" or not o.supplier_id or o.expected_date < start or o.expected_date > end:
            continue
        a = arts.get(o.article_id)
        if a is None:
            continue
        s = sups.get(o.supplier_id)
        kind = mode_kind(s.transport_mode if s else "")
        if kind not in wanted:
            continue
        carrier = (s.transport_mode.strip() if s and kind == "transporteur" else "")
        row = rows.setdefault(o.supplier_id, {"supplier_id": o.supplier_id, "name": s.name if s else "", "transport_contact": s.transport_contact if s else "",
                                               "emails": carriers.get(carrier, []) if carrier else (s.transport_emails if s else []), "docks": s.dock_list if s else ["B15"],
                                               "mode": s.transport_mode if s else MODE_SUPPLIER, "carrier": carrier, "auto": bool(s.transport_auto) if s else False,
                                               "recipient": (f"carrier:{carrier}" if carrier else f"sup:{o.supplier_id}"), "orders": []})
        row["orders"].append({"order_id": o.order_id, "article_id": o.article_id, "designation": a.designation, "unit": a.unit, "ref": o.ref or "",
                              "expected_date": o.expected_date, "qty_ordered": float(o.qty_ordered), "qty_open": float(o.qty_open or 0.0),
                              "ignored": (o.article_id, o.supplier_id, o.expected_date) in ignored, "transports": by_order.get(o.order_id, [])})
    for r in rows.values():
        r["orders"].sort(key=lambda x: (x["expected_date"], x["article_id"], x["ref"]))
    names = {sid: s.name for sid, s in sups.items()}
    return {"as_of": as_of, "start": start, "end": end, "days": days, "docks": list(DOCKS), "scope": scope, "plan_weekday": plan_weekday, "slots": slots or [],
            "suppliers": sorted(rows.values(), key=lambda r: (r["name"].upper(), r["supplier_id"])),
            "transports": [transport_out(t, names) for t in transports]}


# ----------------------------------------------------------------------------- creation
class TransportError(ValueError):
    pass


def create_transports(session: Session, user: str, ds: Dataset, supplier_id: str | None, order_ids: list[str],
                      slots: list[dict[str, Any]], carrier: str = "", note: str = "", maf: bool = False) -> list[AppTransport]:
    """One transport per slot (date, time, docks), every slot carrying the same firm orders : of ``supplier_id``
    (a supplier who delivers himself or through a carrier : the carrier of his mode fills the CSV column), or,
    with ``maf``, of any « MAF Hambach » suppliers (one shuttle).  Does not commit."""
    sups = {s.supplier_id: s for s in ds.suppliers}
    if maf:
        orders = {o.order_id: o for o in ds.orders if o.order_type.value == "FIRM" and o.supplier_id in sups and mode_kind(sups[o.supplier_id].transport_mode) == "maf"}
        missing = [oid for oid in order_ids if oid not in orders]
        if missing:
            raise TransportError(f"Commande(s) inconnue(s) ou hors MAF Hambach : {', '.join(missing)}")
        supplier_id, mode = MAF_ID, "maf"
    else:
        if not supplier_id:
            raise TransportError("Fournisseur manquant")
        sp = sups.get(supplier_id)
        kind = mode_kind(sp.transport_mode if sp else "")
        if kind == "messagerie":
            raise TransportError(f"{supplier_id} : flux messagerie, aucun transport à organiser")
        if kind == "maf":
            raise TransportError(f"{supplier_id} : pièces au MAF Hambach, le transport se crée dans l'onglet Planning MAF")
        orders = {o.order_id: o for o in ds.orders if o.order_type.value == "FIRM" and o.supplier_id == supplier_id}
        missing = [oid for oid in order_ids if oid not in orders]
        if missing:
            raise TransportError(f"Commande(s) inconnue(s) pour le fournisseur {supplier_id} : {', '.join(missing)}")
        mode = "transporteur" if kind == "transporteur" else "fournisseur"
        if kind == "transporteur" and not (carrier or "").strip():
            carrier = sp.transport_mode.strip()
    if not slots:
        raise TransportError("Au moins un transport (date, heure, quai) est nécessaire")
    created: list[AppTransport] = []
    for sl in slots:
        time = str(sl.get("time") or "").strip()
        docks = [d for d in dict.fromkeys(str(x).strip() for x in (sl.get("docks") or [])) if d]
        if not TIME_RE.match(time):
            raise TransportError(f"Heure invalide : « {time} » (attendu HH:MM)")
        bad = [d for d in docks if d not in DOCKS]
        if bad or not docks:
            raise TransportError(f"Quai(s) inconnu(s) : « {', '.join(bad) or 'aucun'} » (attendu {', '.join(DOCKS)})")
        day = sl["date"] if isinstance(sl["date"], dt.date) else dt.date.fromisoformat(str(sl["date"]))
        t = AppTransport(transport_id=next_transport_id(session), supplier_id=supplier_id, date=day, time=time, docks=";".join(docks),
                         carrier=(carrier or "").strip()[:120], note=(note or "").strip()[:500], mode=mode, status="planned", created_by=user)
        for oid in dict.fromkeys(order_ids):
            o = orders[oid]
            t.orders.append(AppTransportOrder(order_id=oid, article_id=o.article_id, supplier_id=o.supplier_id or "", ref=o.ref or "", expected_date=o.expected_date,
                                              qty=float(o.qty_open if o.qty_open is not None else o.qty_ordered)))
        session.add(t)
        session.flush()
        audit(session, user, "create", "transport", t.transport_id, None,
              {"supplier_id": supplier_id, "mode": mode, "date": day.isoformat(), "time": time, "docks": docks, "carrier": t.carrier, "orders": list(dict.fromkeys(order_ids))})
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
    """Header then **one line per transport and per dock** (identifier and every other field repeated), ``;``
    separated, CRLF (the tool runs on Windows) :
    ``EM000001;ENNOVI HUNGARY;;03/11/2026;14:00:00;S-000517;R;;;B15``."""
    lines = [";".join(CSV_HEADER)]
    for t in transports:
        for dock in split_docks(t.docks) or [""]:
            shuttle = t.mode == "maf" or t.supplier_id == MAF_ID
            cells = [t.transport_id, MAF_LABEL if shuttle else names.get(t.supplier_id, t.supplier_id), t.carrier or "", f"{t.date:%d/%m/%Y}", f"{t.time}:00",
                     MAF_LABEL if shuttle else t.supplier_id, FLOW, "", "", dock]
            lines.append(";".join(c.replace(";", " ") for c in cells))
    return "\r\n".join(lines) + "\r\n"


def csv_filename(transports: list[AppTransport]) -> str:
    ids = [t.transport_id for t in transports]
    return f"ATR_{ids[0]}.csv" if len(ids) == 1 else f"ATR_{ids[0]}-{ids[-1]}.csv"


def _qty(v: float, unit: str) -> str:
    s = f"{v:,.3f}".rstrip("0").rstrip(".").replace(",", " ")
    return f"{s} {unit}".strip()


def recipients(transports: list[AppTransport], suppliers: dict[str, Supplier], carriers: dict[str, list[str]]) -> list[str]:
    """Who receives the notice : the carrier's contacts when the transport goes through a carrier, the transport
    contacts of every supplier carried by a shuttle, the supplier's transport contacts otherwise."""
    out: list[str] = []
    for t in transports:
        if t.mode == "transporteur" and t.carrier:
            emails = carriers.get(t.carrier.strip(), [])
        elif t.mode == "maf" or t.supplier_id == MAF_ID:
            emails = [e for o in t.orders for e in (suppliers[o.supplier_id].transport_emails if o.supplier_id in suppliers else [])]
        else:
            emails = suppliers[t.supplier_id].transport_emails if t.supplier_id in suppliers else []
        for e in emails:
            if e not in out:
                out.append(e)
    return out


def mail_parts(transports: list[AppTransport], supplier: Supplier | None, articles: dict[str, Any], user: str,
               suppliers: dict[str, Supplier] | None = None, carriers: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Recipients (``recipients``), subject and body of the notice, in French then English : identifiers, dates,
    times and docks of the transports, then the orders they carry (with their supplier on a shuttle)."""
    suppliers = suppliers if suppliers is not None else ({supplier.supplier_id: supplier} if supplier else {})
    shuttle = bool(transports) and (transports[0].mode == "maf" or transports[0].supplier_id == MAF_ID)
    name = MAF_LABEL if shuttle else (supplier.name if supplier else (transports[0].supplier_id if transports else ""))
    ids = ", ".join(t.transport_id for t in transports)
    refs: list[str] = []
    for t in transports:
        for r in _refs(t):
            if r not in refs:
                refs.append(r)
    first = transports[0]
    when = f"{first.date:%d/%m/%Y} {first.time}" if len(transports) == 1 else f"{first.date:%d/%m/%Y}" + ("" if all(t.date == first.date for t in transports) else " …")
    docks = sorted({d for t in transports for d in split_docks(t.docks)})
    subject = f"Transport {ids} – {name} – livraison {when} – quai {' / '.join(docks)}" + (f" – commande(s) {', '.join(refs)}" if refs else "")
    plural = len(transports) > 1
    lines = ["Bonjour,", "",
             f"Merci de livrer les commandes ci-dessous selon {'les transports suivants' if plural else 'le transport suivant'} ; "
             f"{'les identifiants de transport sont' if plural else 'l’identifiant de transport est'} à rappeler sur les documents de livraison et au chauffeur :", ""]
    for t in transports:
        lines.append(f"  • {t.transport_id} – {t.date:%d/%m/%Y} à {t.time} – quai {' / '.join(split_docks(t.docks))}" + (f" – transporteur {t.carrier}" if t.carrier else ""))
    lines += ["", "Commandes concernées :"]
    for o in first.orders:
        a = articles.get(o.article_id)
        desig = f" {a.designation}" if a is not None and a.designation else ""
        unit = a.unit if a is not None else ""
        who = f" – {suppliers[o.supplier_id].name}" if shuttle and o.supplier_id in suppliers else ""
        lines.append(f"  • {o.ref or o.order_id}{who} – {o.article_id}{desig} – {_qty(o.qty, unit)}" + (f" – prévue le {o.expected_date:%d/%m/%Y}" if o.expected_date else ""))
    if first.note:
        lines += ["", first.note]
    lines += ["", "Cordialement,", user, "", "---", "",
              f"Please deliver the orders below according to the transport{'s' if plural else ''} above ; the transport ID{'s' if plural else ''} must appear on the delivery documents and be known to the driver."]
    return {"to": recipients(transports, suppliers, carriers or {}), "subject": subject, "body": "\n".join(lines)}
