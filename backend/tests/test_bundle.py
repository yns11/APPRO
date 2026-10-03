"""Contrôles statiques avant déploiement, exécutés à chaque passage de la suite.

* le vérificateur de la compétence ``databricks-livraison`` (pannes réellement observées dont le
  symptôme désigne la mauvaise cause) ne remonte aucune anomalie bloquante ;
* ``app.yaml`` (déploiement direct) et le bloc ``config`` de la ressource App du bundle disent la
  même chose : commande et variables d'environnement ;
* un réglage présent à la fois dans le manifeste et dans le code a la même valeur des deux côtés
  (le manifeste gagne, en silence, et seulement en production).
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE / ".claude" / "skills" / "databricks-livraison"))

from verifier_bundle import verifier  # noqa: E402

from appro.config import Settings  # noqa: E402


def test_le_bundle_ne_porte_aucune_anomalie_connue() -> None:
    bloquantes = [a for a in verifier(RACINE) if a.bloquant]
    assert not bloquantes, "\n\n" + "\n\n".join(str(a) for a in bloquantes)


def _env(entries: list[dict]) -> dict[str, str]:
    out = {}
    for e in entries:
        out[e["name"]] = e.get("value") if "value" in e else f"from:{e.get('valueFrom') or e.get('value_from')}"
    return out


def test_app_yaml_et_bundle_disent_la_meme_chose() -> None:
    manifeste = yaml.safe_load((RACINE / "app.yaml").read_text(encoding="utf-8"))
    bundle = yaml.safe_load((RACINE / "resources" / "appro_app.yml").read_text(encoding="utf-8"))
    config = bundle["resources"]["apps"]["appro"]["config"]
    assert manifeste["command"] == config["command"] == ["python", "main.py"]
    a, b = _env(manifeste["env"]), _env(config["env"])
    assert set(a) == set(b), f"variables différentes : {set(a) ^ set(b)}"
    assert a["LAKEBASE_ENDPOINT"] == b["LAKEBASE_ENDPOINT"] == "from:postgres"
    # les valeurs figées (non paramétrées par une variable de bundle) sont identiques
    for k, v in b.items():
        if "${" not in str(v):
            assert a[k] == v, (k, a[k], v)


def test_le_manifeste_ne_fige_pas_d_ancienne_valeur() -> None:
    env = _env(yaml.safe_load((RACINE / "app.yaml").read_text(encoding="utf-8"))["env"])
    s = Settings(_env_file=None)
    for variable, attendu in (("APPRO_HORIZON_DAYS", s.horizon_days), ("APPRO_HISTORY_WEEKS", s.history_weeks),
                              ("APPRO_UC_CATALOG", s.uc_catalog), ("APPRO_UC_SCHEMA", s.uc_schema),
                              ("APPRO_ERP_ORDERS_TABLE", s.erp_orders_table), ("APPRO_ERP_RECEIPTS_TABLE", s.erp_receipts_table),
                              ("APPRO_DB_SCHEMA", s.db_schema)):
        assert str(env[variable]) == str(attendu), f"{variable} vaut {env[variable]} dans app.yaml et {attendu} dans le code"


def test_le_job_et_l_application_partagent_le_sql_de_correspondance() -> None:
    """Le notebook duplique le SQL de erp_sql.py : les deux doivent nommer les mêmes colonnes."""
    from appro.data.erp_sql import ErpTables, fact_queries
    from appro.data.schemas import TABLES
    q = fact_queries(ErpTables(consumption="prod", pdp="pdp"))
    assert set(q) == {"fct_purchase_orders", "fct_receipts", "fct_consumption_actual", "fct_production_plan"}
    for name, sql in q.items():
        for col in TABLES[name].column_names:
            assert f"AS {col}" in sql, (name, col)
    notebook = (RACINE / "jobs" / "sync_erp_to_lakebase_notebook.py").read_text(encoding="utf-8")
    for name in q:
        for col in TABLES[name].column_names:
            assert f'"{col}"' in notebook, (name, col)
    job = (RACINE / "jobs" / "sync_erp_to_lakebase.py").read_text(encoding="utf-8")
    assert "sys.exit" not in job and "raise SystemExit" not in job


def test_chaque_cible_de_production_a_son_schema() -> None:
    """Toutes les cibles peuvent partager le projet Lakebase : chaque App (principal de service distinct)
    doit posséder son propre schéma, sinon « permission denied for schema public » à la première table."""
    bundle = yaml.safe_load((RACINE / "databricks.yml").read_text(encoding="utf-8"))
    default = bundle["variables"]["app_schema"]["default"]
    schemas = {name: t.get("variables", {}).get("app_schema", default) for name, t in bundle["targets"].items()}
    assert len(set(schemas.values())) == len(schemas), schemas
