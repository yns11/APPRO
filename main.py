"""Entry point of the Databricks App (and of ``python main.py`` locally).

Databricks Apps deploys the **content** of the repository root into the container, so the
``backend`` folder is right next to this file: it is put on ``sys.path`` before importing the
FastAPI application.  uvicorn is started from here, on the port the platform injects
(``DATABRICKS_APP_PORT``), bound to ``0.0.0.0`` (mandatory on Databricks Apps).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from appro.api.main import app  # noqa: E402  – the path must be set before the import

__all__ = ["app"]


def main() -> None:
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("DATABRICKS_APP_PORT", "8000")),
                workers=int(os.environ.get("APPRO_WORKERS", "2")), timeout_keep_alive=75, access_log=False)


if __name__ == "__main__":
    main()   # never raise SystemExit here: the platform treats it as a failure
