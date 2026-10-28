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
    #: numeric column where « empty » is a value of its own (no cap, inherited lead time) : kept NULL, never 0
    nullable: bool = False

    @property
    def keeps_null(self) -> bool:
        return self.nullable and self.type in ("int", "float")


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
    def nullable_numeric(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.keeps_null)

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
           Column("coverage_target_days", "int", "Couverture cible (j ouvrés)", "jours ouvrés de besoin à couvrir : le stock cible du jour", True, 5),
           Column("alert_red_days", "int", "Seuil rouge (j ouvrés)", "couverture ≤ seuil : critique", True, 2),
           Column("alert_yellow_days", "int", "Seuil orange (j ouvrés)", "couverture ≤ seuil : à surveiller", True, 5),
           Column("overstock_days", "int", "Surstock (j ouvrés)", "couverture ≥ seuil : surstock", True, 30),
           Column("demand_days", "int", "Jours de consommation", "le besoin de la semaine est consommé sur les n premiers jours ouvrés (1 à 5 ; 5 = uniforme)", False, 5),
           Column("sourcing_policy", "str", "Sourcing", "quota (répartition au prorata des quotas) ou priority (fournisseur prioritaire seul)", False, "quota"),
           Column("consumption_source", "str", "Source conso", "erp (consommation déclarée), plan (PDP éclaté : pas de consommation fiable) ou derived (dérivée d'autres articles, table Termes de consommation)", False, "erp"),
           Column("active", "bool", "Actif", "oui / non", True, True),
       ]),
    _t("ref_suppliers", "Fournisseurs", "Fournisseurs (COFOR) et leurs jours de livraison.", ("supplier_id",), [
        Column("supplier_id", "str", "Fournisseur", "code fournisseur (S-000…)", True, "S-000545"),
        Column("name", "str", "Nom", "raison sociale", True, "ELANTAS EUROPE S.R.L."),
        Column("country", "str", "Pays", "", False, "IT"),
        Column("contact", "str", "Contact", "", False, ""),
        Column("transport_contact", "str", "Contact transport", "adresses e-mail des destinataires des avis de transport (séparées par ; ou ,) : tout e-mail de transport part vers ces adresses", False, ""),
        Column("delivery_weekdays", "str", "Jours de livraison", "jours de livraison standard, ISO, ex. 1,2,3,4,5 (1 = lundi)", True, "1,2,3,4,5"),
        Column("strict_days", "bool", "Jours stricts", "oui : livraisons uniquement les jours standard, jamais de proposition hors jour standard (◇) ; non (défaut) : un jour ouvré tenable avant le besoin est proposé quand le jour standard ne l'est plus", False, False),
        Column("lead_time_days", "int", "Délai (j ouvrés)", "délai commande → livraison en jours ouvrés, inclus (lundi + 5 = lundi suivant) ; valeur par défaut des liens", True, 5),
        Column("active", "bool", "Actif", "", True, True),
    ]),
    _t("ref_article_suppliers", "Article ↔ fournisseur", "Règles d'approvisionnement de chaque couple article / fournisseur.",
       ("article_id", "supplier_id"), [
           Column("article_id", "str", "Article", "", True, "P-00001046"),
           Column("supplier_id", "str", "Fournisseur", "", True, "S-000545"),
           Column("moq", "float", "MOQ", "quantité minimale de commande", True, 400),
           Column("pack_qty", "float", "UM", "unité de manutention (conditionnement) : les propositions sont arrondies au multiple supérieur", True, 400),
           Column("lead_time_override", "int", "Délai spécifique (j ouvrés)", "exception au délai du fournisseur ; vide = délai du fournisseur", False, "", nullable=True),
           Column("quota_pct", "float", "Quota %", "répartition multi-sourcing (100 si mono-source)", True, 100),
           Column("priority", "int", "Priorité", "1 = fournisseur principal", True, 1),
           Column("max_qty_per_delivery", "float", "Max / livraison", "plafond d'une livraison (multiple de l'UM, ≥ MOQ) ; vide = aucun", False, "", nullable=True),
           Column("max_qty_per_week", "float", "Max / semaine", "plafond d'une semaine ISO, fermes et plan compris (≤ max / livraison × jours de livraison) ; vide = aucun", False, "", nullable=True),
           Column("active", "bool", "Actif", "", True, True),
       ]),
    _t("ref_consumption_terms", "Termes de consommation", "Articles en source de consommation « dérivée » : conso = Σ coefficient × consommation "
       "des articles sources (sources en mode ERP, présentes dans un programme de l'article et dans aucun autre).",
       ("article_id", "source_article_id"), [
           Column("article_id", "str", "Article", "article dont la consommation est dérivée", True, "P-00038992"),
           Column("source_article_id", "str", "Article source", "article dont la consommation déclarée sert de base", True, "P-00041210"),
           Column("coef", "float", "Coefficient", "quantité d'article consommée par unité d'article source", True, 2),
       ]),
    _t("ref_closed_orders", "Commandes fermées (PO)", "Commandes fermées ponctuelles (PO, six chiffres) dont les réceptions comptent dans la ligne Reçu "
       "et le stock, comme celles des commandes ouvertes (CO-…). Les réceptions de toute autre commande restent visibles "
       "dans Recherche, les flux et la fiche fournisseur sans entrer dans le stock.", ("purch_id",), [
           Column("purch_id", "str", "N° de PO", "numéro de la commande fermée (six chiffres)", True, "024596"),
           Column("supplier_id", "str", "Fournisseur", "information : fournisseur de la PO", False, "S-000545"),
           Column("article_id", "str", "Article", "information : article principal de la PO (vide = tous)", False, "P-00038992"),
           Column("note", "str", "Commentaire", "", False, "flux dégradé en attente de CO"),
           Column("active", "bool", "Actif", "inactif : la PO ne compte plus", True, True),
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
           Column("first_seen", "date", "Première apparition", "jour où le créneau est apparu dans l'ERP (posé par le job de synchronisation, "
                  "conservé d'une exécution à l'autre ; vide = inconnu)", False),
       ], reference=False),
    _t("fct_receipts", "Réceptions ERP", "Journaux d'arrivée par fournisseur / commande / BL / article / jour et statut (recep_edi) : "
       "En transit (journal non validé : affiché, jamais compté), Reçu (journal validé, accusé de réception à valider) ou "
       "Comptabilisé (accusé de réception validé).",
       ("receipt_id",), [
           Column("receipt_id", "str", "Identifiant", "fournisseur|commande|BL|article|jour|statut", True),
           Column("article_id", "str", "Article", "", True),
           Column("supplier_id", "str", "Fournisseur", "", True),
           Column("receipt_date", "date", "Date de réception", "date de validation du journal (Reçu, Comptabilisé) ou date de la ligne du journal (En transit)", True),
           Column("qty", "float", "Quantité reçue", "", True),
           Column("purch_id", "str", "N° commande", "", False),
           Column("packing_slip", "str", "N° BL", "plusieurs BL séparés par « | » possibles ; vide = réception manuelle", False),
           Column("status", "str", "Statut", "En transit, Reçu ou Comptabilisé", False),
       ], reference=False),
    _t("fct_consumption_actual", "Consommation réelle", "Consommation réelle journalière par composant (déjà éclatée : "
       "quantité d'article consommée, dans son unité de stock). Alimente le besoin des jours passés et le reliquat de la "
       "semaine en cours.", ("article_id", "date"), [
           Column("article_id", "str", "Article", "référence composant (P-00…), telle que dans le référentiel", True),
           Column("date", "date", "Jour", "jour de consommation", True),
           Column("qty", "float", "Quantité consommée", "unité de stock de l'article ; un 0 déclaré est respecté", True),
       ], reference=False),
    _t("fct_desadv", "DESADV", "Avis d'expédition EDI (DespatchAdvice-Purchase, desadv_edi) : une ligne par ligne d'article d'un message ; "
       "non reçu tant que son BL n'apparaît pas dans les réceptions.", ("desadv_id",), [
           Column("desadv_id", "str", "Identifiant", "Document_ID|ID_Ligne", True),
           Column("article_id", "str", "Article", "", True),
           Column("supplier_id", "str", "Fournisseur", "", True),
           Column("supplier_name", "str", "Nom fournisseur", "", False),
           Column("packing_slip", "str", "N° BL", "référence du bon de livraison (BL) ; clé de rapprochement avec recep_edi", True),
           Column("purch_id", "str", "N° commande", "", False),
           Column("issue_date", "date", "Date d'émission", "date d'émission de l'avis = jour affiché dans la ligne Reçu", True),
           Column("qty", "float", "Quantité annoncée", "", True),
           Column("state", "str", "État du message", "Créé, Traité, Erreur, En attente, Annulé", False),
           Column("final_processing", "str", "Traitement final", "Non traité, OK", False),
           Column("stock_trans_id", "str", "Transaction de stock", "ID_transaction_stock : vide pour un message traité = aucun journal de saisie créé", False),
       ], reference=False),
    _t("fct_prices", "Prix des articles", "Prix de revient standard par article (silver_base_article.std_cost_price, en euros). "
       "Sert à valoriser les stocks ; un article absent n'est pas valorisé, un article présent n'est pas ajouté au référentiel.",
       ("article_id",), [
           Column("article_id", "str", "Article", "item_id", True),
           Column("price", "float", "Prix (€)", "std_cost_price, euros par unité de stock", True),
       ], reference=False),
    _t("fct_stock_snapshot", "Stock ERP (snapshots)", "Stock physique de l'ERP en fin de journée, par article et entrepôt (stock_snapshot : item_id, snapshot_date, "
       "entrepot, stock_physique), entrepôts configurés seulement, 30 derniers jours. Comparé au stock projeté du même jour (écart, dérive, calage) ; "
       "jamais un stock de départ, jamais écrit dans le référentiel.", ("article_id", "snapshot_date", "warehouse"), [
           Column("article_id", "str", "Article", "item_id", True),
           Column("snapshot_date", "date", "Date du snapshot", "stock connu en fin de journée", True),
           Column("warehouse", "str", "Entrepôt", "entrepot (B06, B06VRAC…)", True),
           Column("qty", "float", "Stock physique", "stock_physique, unité de stock de l'article", True),
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
    nullable = set(schema.nullable_numeric)
    for col, typ in schema.column_types.items():
        s = df[col] if col in df.columns else pd.Series([None] * len(df), index=df.index, dtype=object)
        if col in nullable:
            # empty stays empty (None) ; 0 is not a value for these columns (no cap / inherited lead time)
            nums = pd.to_numeric(s, errors="coerce")
            out[col] = pd.Series([None if pd.isna(v) or v <= 0 else (int(v) if typ == "int" else float(v)) for v in nums], index=df.index, dtype=object)
        elif typ == "str":
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
