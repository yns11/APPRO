"""Canonical table schemas (columns, types, keys, French labels).

Two families of tables share the same canonical names everywhere (seed CSV, application database,
Unity Catalog queries, Excel templates, pandas frames):

* **reference tables** (``REFERENCE_TABLES``) – managed **in the application** (CRUD screens and
  Excel import per table), stored in the application database ;
* **ERP fact tables** (``FACT_TABLES``) – read from the ERP extractions (``commandes_edi``,
  ``recep_edi``, production), either through a SQL warehouse or from their Lakebase mirror.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class Column:
    name: str
    type: str          # str | float | int | bool | date
    label: str         # French label used in the Excel templates and the screens
    description: str = ""
    required: bool = False
    example: object = ""


@dataclass(frozen=True)
class TableSchema:
    name: str
    label: str
    description: str
    columns: tuple[Column, ...]
    key: tuple[str, ...]
    reference: bool = True
    labels: dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def column_types(self) -> dict[str, str]:
        return {c.name: c.type for c in self.columns}

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def column(self, name: str) -> Column:
        return next(c for c in self.columns if c.name == name)


def _t(name: str, label: str, description: str, key: tuple[str, ...], columns: list[Column], reference: bool = True) -> TableSchema:
    return TableSchema(name, label, description, tuple(columns), key, reference)


TABLES: dict[str, TableSchema] = {t.name: t for t in [
    # ------------------------------------------------------------------ reference (application)
    _t("ref_articles", "Articles", "Articles approvisionnés : identité, unité, approvisionneur (périmètre), politique de stock.",
       ("article_id",), [
           Column("article_id", "str", "Article", "référence article (P-00…)", True, "P-00001046"),
           Column("designation", "str", "Désignation", "", True, "RESIN"),
           Column("unit", "str", "Unité", "unité de stock : PCE, KG, M", True, "KG"),
           Column("family", "str", "Famille", "famille / groupe (facultatif)", False, ""),
           Column("planner", "str", "Approvisionneur", "code approvisionneur = périmètre de l'application", True, "QUENTIN"),
           Column("coverage_target_days", "int", "Couverture cible (j)", "jours calendaires de besoin à couvrir", True, 10),
           Column("alert_red_days", "int", "Seuil rouge (j)", "couverture ≤ seuil : critique", True, 3),
           Column("alert_yellow_days", "int", "Seuil orange (j)", "couverture ≤ seuil : à surveiller", True, 10),
           Column("overstock_days", "int", "Surstock (j)", "couverture ≥ seuil : surstock", True, 30),
           Column("safety_stock_qty", "float", "Stock de sécurité", "quantité fixe (0 = aucune)", False, 0),
           Column("order_cycle_days", "int", "Cycle de commande (j)", "besoin ajouté au niveau de recomplètement", False, 7),
           Column("active", "bool", "Actif", "oui / non", True, True),
       ]),
    _t("ref_suppliers", "Fournisseurs", "Fournisseurs (COFOR) et leurs jours de livraison.", ("supplier_id",), [
        Column("supplier_id", "str", "Fournisseur", "code fournisseur (S-000…)", True, "S-000545"),
        Column("name", "str", "Nom", "raison sociale", True, "ELANTAS EUROPE S.R.L."),
        Column("country", "str", "Pays", "", False, "IT"),
        Column("contact", "str", "Contact", "", False, ""),
        Column("delivery_weekdays", "str", "Jours de livraison", "jours ISO autorisés, ex. 1,2,3,4,5 (1 = lundi)", True, "1,2,3,4,5"),
        Column("active", "bool", "Actif", "", True, True),
    ]),
    _t("ref_article_suppliers", "Article ↔ fournisseur", "Règles d'approvisionnement de chaque couple article / fournisseur.",
       ("article_id", "supplier_id"), [
           Column("article_id", "str", "Article", "", True, "P-00001046"),
           Column("supplier_id", "str", "Fournisseur", "", True, "S-000545"),
           Column("moq", "float", "MOQ", "quantité minimale de commande", True, 400),
           Column("pack_qty", "float", "PLA", "conditionnement : les propositions sont arrondies au multiple supérieur", True, 400),
           Column("lead_time_days", "int", "Délai (j ouvrés)", "délai fournisseur en jours ouvrés", True, 15),
           Column("quota_pct", "float", "Quota %", "répartition multi-sourcing (100 si mono-source)", True, 100),
           Column("priority", "int", "Priorité", "1 = fournisseur principal", True, 1),
           Column("active", "bool", "Actif", "", True, True),
       ]),
    _t("ref_programs", "Programmes", "Programmes de production (PF / SF) tels que nommés dans le PDP.", ("program_id",), [
        Column("program_id", "str", "Programme", "identifiant (mass-000…)", True, "mass-00040633"),
        Column("name", "str", "Nom", "nom utilisé dans le fichier PDP", True, "Stators M3"),
        Column("family", "str", "Famille", "", False, "M3"),
        Column("active", "bool", "Actif", "", True, True),
    ]),
    _t("ref_bom", "Nomenclatures", "Nomenclature à un niveau : composant consommé par unité produite.", ("program_id", "article_id"), [
        Column("program_id", "str", "Programme", "", True, "mass-00040633"),
        Column("article_id", "str", "Composant", "", True, "P-00043859"),
        Column("qty_per", "float", "Qté / unité", "quantité de composant par unité produite", True, 1),
        Column("unit", "str", "Unité", "", True, "PCE"),
        Column("scrap_pct", "float", "Rebut %", "majore le besoin", False, 0),
        Column("valid_from", "date", "Valide du", "facultatif", False, ""),
        Column("valid_to", "date", "Valide au", "facultatif", False, ""),
    ]),
    _t("fct_stock", "Stock de référence", "Stock physique par article à la fin d'une journée (le plus récent est utilisé).",
       ("article_id", "snapshot_date"), [
           Column("article_id", "str", "Article", "", True, "P-00001046"),
           Column("snapshot_date", "date", "Date du stock", "stock connu en fin de journée", True, dt.date(2026, 9, 18)),
           Column("qty_on_hand", "float", "Stock physique", "", True, 4577.28),
           Column("qty_blocked", "float", "Stock bloqué", "déduit du stock physique", False, 0),
           Column("unit", "str", "Unité", "", False, "KG"),
       ]),
    _t("ref_planners", "Approvisionneurs", "Approvisionneurs et droits : chaque approvisionneur écrit sur son carnet d'articles "
       "(colonne Approvisionneur des articles) ; manager = paramètres pour tous + import des PDP ; admin = tous les droits. "
       "Tant que la table est vide, tout utilisateur est administrateur.",
       ("planner_id",), [
           Column("planner_id", "str", "ID", "identifiant (PROC1, PROC2…)", True, "PROC1"),
           Column("name", "str", "Approvisionneur", "prénom / code tel qu'il figure dans la colonne Approvisionneur des articles", True, "QUENTIN"),
           Column("email", "str", "Email", "identifiant de connexion Databricks", True, "quentin@exemple.com"),
           Column("role", "str", "Rôle", "appro, manager ou admin", True, "appro"),
           Column("active", "bool", "Actif", "inactif = lecture seule", True, True),
       ]),
    _t("ref_delegations", "Délégations", "Un approvisionneur confie l'écriture sur son carnet à un collègue entre deux dates.",
       ("from_planner", "to_planner", "date_from"), [
           Column("from_planner", "str", "Délégant (ID)", "approvisionneur qui délègue son carnet", True, "PROC1"),
           Column("to_planner", "str", "Destinataire (ID)", "approvisionneur qui reçoit l'accès", True, "PROC2"),
           Column("date_from", "date", "Du", "premier jour de la délégation", True, dt.date(2026, 10, 5)),
           Column("date_to", "date", "Au", "dernier jour (vide = sans fin)", False, dt.date(2026, 10, 16)),
           Column("note", "str", "Motif", "", False, "congés"),
           Column("active", "bool", "Active", "", True, True),
       ]),
    # ------------------------------------------------------------------ ERP facts
    _t("fct_purchase_orders", "Commandes ERP", "Créneaux de livraison ERP : fournisseur | article | date | ferme (commandes_edi).",
       ("order_id",), [
           Column("order_id", "str", "Identifiant", "vendaccount|itemid|yyyyMMdd|silfirmorder", True),
           Column("article_id", "str", "Article", "", True),
           Column("supplier_id", "str", "Fournisseur", "", True),
           Column("supplier_name", "str", "Nom fournisseur", "", False),
           Column("order_type", "str", "Type", "FIRM (Ordre_ferme = Oui) ou FORECAST", True),
           Column("expected_date", "date", "Date de livraison", "", True),
           Column("qty_ordered", "float", "Quantité commandée", "", True),
           Column("qty_open", "float", "Quantité restante", "restant ERP (non fiable une fois la date passée)", True),
           Column("purch_id", "str", "N° commande", "numéros de commande d'achat (information)", False),
           Column("commitment", "str", "Niveau d'engagement", "Ferme / Prévisionnel (information)", False),
       ], reference=False),
    _t("fct_receipts", "Réceptions ERP", "Réceptions physiques par fournisseur / commande / BL / article / jour (recep_edi).",
       ("receipt_id",), [
           Column("receipt_id", "str", "Identifiant", "", True),
           Column("article_id", "str", "Article", "", True),
           Column("supplier_id", "str", "Fournisseur", "", True),
           Column("receipt_date", "date", "Date de réception", "", True),
           Column("qty", "float", "Quantité reçue", "", True),
           Column("purch_id", "str", "N° commande", "", False),
           Column("packing_slip", "str", "N° BL", "", False),
       ], reference=False),
    _t("fct_production_actual", "Production réelle", "Production réelle journalière par programme.", ("program_id", "date"), [
        Column("program_id", "str", "Programme", "", True),
        Column("date", "date", "Jour", "", True),
        Column("qty", "float", "Quantité produite", "", True),
    ], reference=False),
    _t("fct_production_plan", "PDP ERP", "Plan de production hebdomadaire (facultatif : le PDP est importé par fichier).",
       ("program_id", "week_start", "version"), [
           Column("program_id", "str", "Programme", "", True),
           Column("week_start", "date", "Lundi", "lundi de la semaine ISO", True),
           Column("qty", "float", "Quantité", "", True),
           Column("version", "str", "Version", "la plus récente gagne", False),
       ], reference=False),
]}

REFERENCE_TABLES: tuple[str, ...] = tuple(n for n, t in TABLES.items() if t.reference)
FACT_TABLES: tuple[str, ...] = tuple(n for n, t in TABLES.items() if not t.reference)


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return False
    return str(v).strip().lower() in ("true", "1", "yes", "oui", "y", "t", "vrai", "x")


def coerce(df: pd.DataFrame, schema: TableSchema) -> pd.DataFrame:
    """Coerce a frame to the canonical schema (missing columns added, types normalised)."""
    out = pd.DataFrame(index=df.index)
    for col, typ in schema.column_types.items():
        s = df[col] if col in df.columns else pd.Series([None] * len(df), index=df.index, dtype=object)
        if typ == "str":
            out[col] = s.map(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip())
        elif typ == "float":
            out[col] = pd.to_numeric(s, errors="coerce").fillna(0.0).astype(float)
        elif typ == "int":
            out[col] = pd.to_numeric(s, errors="coerce").fillna(0).astype(int)
        elif typ == "bool":
            out[col] = s.map(_to_bool).astype(bool)
        elif typ == "date":
            parsed = pd.to_datetime(s, errors="coerce")
            out[col] = pd.Series([None if pd.isna(v) else v.date() for v in parsed], index=df.index, dtype=object)
        else:
            raise ValueError(typ)
    return out.reset_index(drop=True)


def parse_weekdays(spec: str | None) -> frozenset[int]:
    if not spec:
        return frozenset({1, 2, 3, 4, 5})
    return frozenset(int(x) for x in str(spec).replace(";", ",").split(",") if x.strip())


def as_date(v) -> dt.date | None:
    if v is None or v is pd.NaT or (isinstance(v, float) and pd.isna(v)) or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])
