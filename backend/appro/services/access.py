"""Who may write what: planners, delegations and roles (docs/regles_metier.md § 11).

* a **reader** (not listed in ``ref_planners``) reads everything and writes nothing ;
* an **appro** writes on the articles of his portfolio (``ref_articles.planner`` = his name) and on
  the portfolios delegated to him (``ref_delegations``, to him, active, today inside the dates) ;
* a **manager** is an appro who also sets the parameters for everybody (global rules, weekly values
  of any article) and imports the PDP ;
* an **admin** has every right (reference tables, planners, delegations included) ;
* while ``ref_planners`` is empty every user is an admin (first installation).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd
from fastapi import HTTPException

ROLES = ("appro", "manager", "admin")
READER = "reader"
# tables an appro may edit for his own articles (row scoped by ``article_id`` or ``planner``)
ARTICLE_TABLES = ("ref_articles", "ref_article_suppliers", "ref_bom", "fct_stock")
# tables reserved to admins (a manager edits the others as a whole)
ADMIN_TABLES = ("ref_planners",)


@dataclass
class Access:
    user: str
    role: str = READER                    # reader | appro | manager | admin
    planner_id: str | None = None
    name: str | None = None               # value of ``ref_articles.planner`` for this user
    portfolio: frozenset[str] = frozenset()   # upper-cased planner names the user may write on
    delegated_from: list[str] = field(default_factory=list)   # planner ids delegating to the user
    bootstrap: bool = False               # no planner declared yet: everybody is admin
    _article_planner: dict[str, str] = field(default_factory=dict, repr=False)
    _planner_name: dict[str, str] = field(default_factory=dict, repr=False)

    # ---- capabilities
    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_manager(self) -> bool:
        return self.role in ("manager", "admin")

    @property
    def can_write(self) -> bool:
        return self.role != READER

    def can_edit_planner(self, planner_name: str | None) -> bool:
        return self.is_admin or (planner_name or "").upper() in self.portfolio

    def can_edit_article(self, article_id: str) -> bool:
        if self.is_admin:
            return True
        planner = self._article_planner.get(article_id)
        return planner is not None and planner.upper() in self.portfolio

    def can_edit_table(self, name: str) -> bool:
        """Whole-table rights (import / replace, add any row)."""
        if self.is_admin:
            return True
        return self.is_manager and name not in ADMIN_TABLES

    def can_edit_row(self, name: str, row: dict) -> bool:
        if self.can_edit_table(name):
            return True
        if not self.can_write:
            return False
        if name in ARTICLE_TABLES:
            aid = str(row.get("article_id") or "")
            if name == "ref_articles" and aid not in self._article_planner:
                return self.can_edit_planner(str(row.get("planner") or ""))   # new article: in his portfolio
            if name == "ref_articles" and "planner" in row and not self.can_edit_planner(str(row.get("planner") or "")):
                return False   # cannot give his article away to someone else
            return self.can_edit_article(aid)
        if name == "ref_delegations":
            return str(row.get("from_planner") or "") == (self.planner_id or "")
        return False

    # ---- guards (HTTP 403)
    def _deny(self, what: str) -> HTTPException:
        who = f"{self.user} ({self.role})"
        return HTTPException(403, f"Accès en lecture seule : {what} – utilisateur {who}")

    def require_write(self) -> None:
        if not self.can_write:
            raise self._deny("vous n'êtes pas déclaré comme approvisionneur")

    def require_article(self, article_id: str) -> None:
        if not self.can_edit_article(article_id):
            owner = self._article_planner.get(article_id)
            raise self._deny(f"l'article {article_id} est hors de votre carnet" + (f" (approvisionneur {owner})" if owner else ""))

    def require_article_params(self, article_id: str) -> None:
        """Weekly values of an article: its planner (or a delegate), a manager or an admin."""
        if not (self.is_manager or self.can_edit_article(article_id)):
            self.require_article(article_id)

    def require_params(self) -> None:
        if not self.is_manager:
            raise self._deny("les règles globales sont réservées aux managers et administrateurs")

    def require_pdp(self) -> None:
        if not self.is_manager:
            raise self._deny("l'import du PDP est réservé aux managers et administrateurs")

    def require_table(self, name: str) -> None:
        if not self.can_edit_table(name):
            raise self._deny(f"la table {name} n'est modifiable en bloc que par " + ("un administrateur" if name in ADMIN_TABLES else "un manager ou un administrateur"))

    def require_row(self, name: str, row: dict) -> None:
        if not self.can_edit_row(name, row):
            raise self._deny(f"cette ligne de {name} est hors de votre carnet")

    def to_dict(self) -> dict:
        return {"user": self.user, "role": self.role, "planner_id": self.planner_id, "name": self.name,
                "portfolio": sorted(self.portfolio), "delegated_from": list(self.delegated_from), "bootstrap": self.bootstrap,
                "can_write": self.can_write, "can_edit_all": self.is_admin, "can_manage_params": self.is_manager,
                "can_import_pdp": self.is_manager, "is_admin": self.is_admin}


def _s(v) -> str:
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()


def resolve_access(user: str, planners: pd.DataFrame, delegations: pd.DataFrame, articles: pd.DataFrame,
                   today: dt.date | None = None, admins: set[str] | None = None) -> Access:
    """Rights of ``user`` (the e-mail forwarded by Databricks Apps) from the three reference frames.

    ``admins`` (``APPRO_ADMINS``, the bundle deployer by default) are administrators whatever the
    planners table says: the person who installs the application can never lock herself out."""
    today = today or dt.date.today()
    art_planner = {str(r["article_id"]): _s(r.get("planner")) for r in articles.to_dict("records")}
    names = {_s(r["planner_id"]): _s(r["name"]) for r in planners.to_dict("records")}
    acc = Access(user=user, _article_planner=art_planner, _planner_name=names)
    forced_admin = (user or "").strip().lower() in (admins or set())
    if planners.empty:
        acc.role, acc.bootstrap = "admin", True
        return acc
    me = [r for r in planners.to_dict("records") if _s(r.get("email")).lower() == (user or "").strip().lower() and bool(r.get("active", True))]
    if not me:
        if forced_admin:
            acc.role = "admin"
        return acc
    row = me[0]
    role = _s(row.get("role")).lower()
    acc.role = "admin" if forced_admin else (role if role in ROLES else "appro")
    acc.planner_id, acc.name = _s(row["planner_id"]), _s(row["name"])
    portfolio = {acc.name.upper()} if acc.name else set()
    for d in delegations.to_dict("records"):
        if _s(d.get("to_planner")) != acc.planner_id or not bool(d.get("active", True)):
            continue
        start, end = d.get("date_from"), d.get("date_to")
        start = None if start is None or (isinstance(start, float) and pd.isna(start)) else start
        end = None if end is None or (isinstance(end, float) and pd.isna(end)) else end
        if (start is not None and today < start) or (end is not None and today > end):
            continue
        giver = _s(d.get("from_planner"))
        if giver in names and names[giver]:
            portfolio.add(names[giver].upper())
            acc.delegated_from.append(giver)
    acc.portfolio = frozenset(portfolio)
    return acc
