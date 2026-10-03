"""Connexion à Lakebase depuis un job Databricks (qui ne reçoit rien de la plateforme).

Une App reçoit ``PGHOST`` / ``PGDATABASE`` / ``PGUSER`` parce qu'une ressource ``postgres`` lui est
attachée ; un job n'a pas de ressources.  Il connaît la **branche** et l'**endpoint** Lakebase,
passés en paramètres depuis les mêmes variables de bundle que la ressource de l'App, et en déduit
l'hôte, l'identité qui l'exécute et un identifiant OAuth frais.

Les variables d'environnement ``PGHOST`` / ``PGUSER`` / ``PGPASSWORD`` restent prioritaires quand
elles sont présentes (exécution locale, secret scope).

Le SDK d'un environnement serverless est figé (souvent antérieur à ``w.postgres``) : les appels
REST de l'API Lakebase sont émis directement par le client HTTP du SDK quand la façade typée
manque, ce qui rend ce module indépendant de la version.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("appro.lakebase")

ENDPOINT_PATH = "/api/2.0/postgres/{endpoint}"
ENDPOINTS_PATH = "/api/2.0/postgres/{branch}/endpoints"
CREDENTIALS_PATH = "/api/2.0/postgres/credentials"


def _workspace():
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient()


def _sdk_version() -> str:
    try:
        from databricks.sdk.version import __version__
        return __version__
    except Exception:
        return "inconnue"


def _get(obj: Any, *path: str) -> Any:
    for p in path:
        if obj is None:
            return None
        obj = obj.get(p) if isinstance(obj, dict) else getattr(obj, p, None)
    return obj


def endpoint_host(client, endpoint: str | None, branch: str | None) -> tuple[str, str]:
    """(endpoint name, host) : the endpoint given, else the read-write endpoint of the branch."""
    api = getattr(client, "postgres", None)
    if endpoint:
        ep = api.get_endpoint(endpoint) if api is not None and hasattr(api, "get_endpoint") \
            else client.api_client.do("GET", ENDPOINT_PATH.format(endpoint=endpoint))
        host = _get(ep, "status", "hosts", "host")
        if not host:
            raise RuntimeError(f"L'endpoint {endpoint} n'expose pas d'hôte (status.hosts.host) : est-il démarré ?")
        return endpoint, str(host)
    if not branch:
        raise RuntimeError("Ni --endpoint ni --branch : impossible de trouver l'hôte Lakebase")
    if api is not None and hasattr(api, "list_endpoints"):
        endpoints = list(api.list_endpoints(branch))
    else:
        endpoints = (client.api_client.do("GET", ENDPOINTS_PATH.format(branch=branch)) or {}).get("endpoints", [])
    for ep in endpoints:
        if "READ_WRITE" in str(_get(ep, "status", "endpoint_type") or _get(ep, "spec", "endpoint_type") or ""):
            host = _get(ep, "status", "hosts", "host")
            if host:
                return str(_get(ep, "name")), str(host)
    raise RuntimeError(f"Aucun endpoint en écriture démarré sur {branch}")


def credential(client, endpoint: str) -> str:
    api = getattr(client, "postgres", None)
    if api is not None and hasattr(api, "generate_database_credential"):
        try:
            return api.generate_database_credential(endpoint=endpoint).token
        except TypeError:
            return api.generate_database_credential(endpoint).token
    body = client.api_client.do("POST", CREDENTIALS_PATH, body={"endpoint": endpoint}) or {}
    token = body.get("token")
    if not token:
        raise RuntimeError(f"Réponse sans jeton de {CREDENTIALS_PATH} : {sorted(body)}")
    return token


def conninfo(endpoint: str | None, branch: str | None, database: str, pg_host: str | None = None,
             pg_user: str | None = None) -> str:
    """The psycopg connection string, discovered rather than expected."""
    host = os.environ.get("PGHOST") or pg_host
    user = os.environ.get("PGUSER") or pg_user
    password = os.environ.get("PGPASSWORD")
    if not (host and user and password):
        client = _workspace()
        log.info("SDK Databricks %s", _sdk_version())
        name, found_host = endpoint_host(client, endpoint, branch)
        host = host or found_host
        user = user or client.current_user.me().user_name
        password = password or credential(client, name)
        log.info("Lakebase : hôte %s, identité %s, base %s (endpoint %s)", host, user, database, name)
    port = os.environ.get("PGPORT", "5432")
    sslmode = os.environ.get("PGSSLMODE", "require")
    return f"host={host} port={port} dbname={database} user={user} password={password} sslmode={sslmode}"
