"""Business validations of the reference rows beyond the column types : supplier link caps and
derived-consumption terms (docs/regles_metier.md)."""
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..data import reference


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f   # NaN


def _is_multiple(qty: float, step: float) -> bool:
    if step <= 0:
        return True
    r = qty / step
    return abs(r - round(r)) < 1e-6


def check_links(rows: Iterable[dict[str, Any]], suppliers: dict[str, dict[str, Any]]) -> None:
    """Caps : multiple of the UM and ≥ MOQ per delivery ; weekly ≤ per delivery × delivery days of the
    supplier ; lead time override ≥ 1 working day."""
    for r in rows:
        who = f"{r.get('article_id')} / {r.get('supplier_id')}"
        pack = _num(r.get("pack_qty")) or 0.0
        moq = _num(r.get("moq")) or 0.0
        per = _num(r.get("max_qty_per_delivery"))
        week = _num(r.get("max_qty_per_week"))
        lead = _num(r.get("lead_time_override"))
        if lead is not None and lead < 1:
            raise ValueError(f"{who} : le délai spécifique doit être d'au moins 1 jour ouvré (vide = délai du fournisseur)")
        if per is not None:
            if per <= 0:
                raise ValueError(f"{who} : le plafond par livraison doit être positif (vide = aucun plafond)")
            if pack > 0 and not _is_multiple(per, pack):
                raise ValueError(f"{who} : le plafond par livraison ({per:g}) doit être un multiple de l'UM ({pack:g})")
            if per < moq:
                raise ValueError(f"{who} : le plafond par livraison ({per:g}) doit être ≥ MOQ ({moq:g})")
        if week is not None:
            if week <= 0:
                raise ValueError(f"{who} : le plafond par semaine doit être positif (vide = aucun plafond)")
            if moq > 0 and week < moq - 1e-9:
                raise ValueError(f"{who} : le plafond par semaine ({week:g}) doit être ≥ MOQ ({moq:g}) : sinon aucune livraison conforme n'est possible")
            if pack > 0 and not _is_multiple(week, pack):
                raise ValueError(f"{who} : le plafond par semaine ({week:g}) doit être un multiple de l'UM ({pack:g})")
            if per is not None:
                sup = suppliers.get(str(r.get("supplier_id")), {})
                days = [d for d in str(sup.get("delivery_weekdays") or "1,2,3,4,5").split(",") if d.strip()]
                limit = per * max(1, len(days))
                if week > limit + 1e-6:
                    raise ValueError(f"{who} : le plafond par semaine ({week:g}) dépasse le plafond par livraison × jours de livraison "
                                     f"({per:g} × {len(days)} = {limit:g})")


def eligible_sources(session: Session, article_id: str) -> list[dict[str, Any]]:
    """Articles that may serve as a consumption source of ``article_id`` : ERP consumption mode, present in
    one of the programmes of the article and in no programme the article is absent from (so that the
    reported consumption of the source only reflects programmes that also consume the article)."""
    arts = {r["article_id"]: r for r in reference.rows_out(session, "ref_articles")}
    progs: dict[str, set[str]] = {}
    for b in reference.rows_out(session, "ref_bom"):
        progs.setdefault(str(b["article_id"]), set()).add(str(b["program_id"]))
    mine = progs.get(article_id, set())
    out = []
    for aid, a in arts.items():
        if aid == article_id or not mine:
            continue
        theirs = progs.get(aid, set())
        if not theirs or not (theirs & mine) or (theirs - mine):
            continue
        if str(a.get("consumption_source") or "erp") != "erp":
            continue
        out.append({"article_id": aid, "designation": a.get("designation", ""), "unit": a.get("unit", ""),
                    "programs": sorted(theirs), "shared_programs": sorted(theirs & mine)})
    return sorted(out, key=lambda r: r["article_id"])


def check_terms(session: Session, rows: Iterable[dict[str, Any]], replace: bool = False) -> None:
    """Derived-consumption terms : coefficient > 0, source ≠ article, source eligible (see
    :func:`eligible_sources`), no cycle through the other terms."""
    rows = list(rows)
    existing = [] if replace else reference.rows_out(session, "ref_consumption_terms")
    edges: dict[str, set[str]] = {}
    for r in existing + rows:
        edges.setdefault(str(r["article_id"]), set()).add(str(r["source_article_id"]))
    cache: dict[str, set[str]] = {}
    for r in rows:
        aid, src = str(r["article_id"]), str(r["source_article_id"])
        coef = _num(r.get("coef"))
        if coef is None or coef <= 0:
            raise ValueError(f"{aid} ← {src} : le coefficient doit être strictement positif")
        if src == aid:
            raise ValueError(f"{aid} : un article ne peut pas être sa propre source de consommation")
        if aid not in cache:
            cache[aid] = {e["article_id"] for e in eligible_sources(session, aid)}
        if src not in cache[aid]:
            raise ValueError(f"{aid} ← {src} : l'article source doit être en consommation ERP, présent dans un programme de "
                             f"{aid} et dans aucun programme où {aid} est absent")
        # cycle : following the sources from src must never come back to aid
        seen, stack = set(), [src]
        while stack:
            cur = stack.pop()
            if cur == aid:
                raise ValueError(f"{aid} ← {src} : boucle de dérivation (la source dépend elle-même de {aid})")
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(edges.get(cur, ()))


CALENDARS = ("usine", "fournisseur", "transit", "quai")
CALENDAR_KINDS = ("fermeture", "ouvert")


def check_calendars(rows: list[dict], supplier_ids: set[str]) -> None:
    """Rows of the Calendriers table : known calendar and nature, a key where one is needed, dates in order."""
    import datetime as dt

    import pandas as pd
    for r in rows:
        cal = str(r.get("calendar") or "").strip().lower()
        if cal not in CALENDARS:
            raise ValueError(f"calendrier « {r.get('calendar')} » inconnu : {', '.join(CALENDARS)}")
        r["calendar"] = cal
        kind = str(r.get("kind") or "fermeture").strip().lower() or "fermeture"
        if kind not in CALENDAR_KINDS:
            raise ValueError(f"nature « {r.get('kind')} » inconnue : {', '.join(CALENDAR_KINDS)}")
        if kind == "ouvert" and cal != "usine":
            raise ValueError("un jour exceptionnellement ouvert ne se déclare que sur le calendrier usine")
        r["kind"] = kind
        key = str(r.get("key") or "").strip()
        if cal == "usine":
            r["key"] = ""
        elif not key:
            raise ValueError(f"calendrier {cal} : la clé est obligatoire (fournisseur, pays ou quai)")
        elif cal == "fournisseur" and key not in supplier_ids:
            raise ValueError(f"calendrier fournisseur : fournisseur inconnu « {key} »")
        a, b = r.get("date_from"), r.get("date_to")
        a = pd.Timestamp(a).date() if a not in (None, "") else None
        b = pd.Timestamp(b).date() if b not in (None, "") and not (isinstance(b, float) and pd.isna(b)) else None
        if a is None:
            raise ValueError("calendrier : la date de début est obligatoire")
        if b is not None and b < a:
            raise ValueError(f"calendrier : la fin ({b.isoformat()}) précède le début ({a.isoformat()})")
        if b is not None and (b - a) > dt.timedelta(days=400):
            raise ValueError("calendrier : une période ne peut pas dépasser 400 jours")
