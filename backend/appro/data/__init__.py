"""Data access layer.

* :mod:`appro.data.schemas`   – canonical table schemas shared by all sources
* :mod:`appro.data.sources`   – ERP / reference data sources (local seed CSV, Unity Catalog)
* :mod:`appro.data.store`     – transactional app store (SQLAlchemy: SQLite locally, Lakebase Postgres on Databricks)
* :mod:`appro.data.assembler` – merges ERP data + app entries into an engine :class:`Dataset`
"""
