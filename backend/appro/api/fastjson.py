"""JSON responses for the large grid payloads, bypassing the Pydantic validation of the response.

FastAPI validates (and copies) a ``response_model`` field by field: for a 500-article grid that is
millions of floats checked one by one, slower than the engine itself.  The presenters build plain
dicts / lists / NumPy arrays and these helpers serialise them directly (``orjson`` when installed,
the standard library otherwise)."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
from enum import Enum
from typing import Any

import numpy as np
from fastapi.responses import Response

try:  # optional, 5-10× faster on large payloads
    import orjson
except ImportError:  # pragma: no cover - depends on the environment
    orjson = None


def _default(o: Any) -> Any:
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, Enum):
        return o.value
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    raise TypeError(f"not serialisable: {type(o).__name__}")


def dumps(data: Any) -> bytes:
    if orjson is not None:
        return orjson.dumps(data, default=_default, option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS)
    return json.dumps(data, default=_default, separators=(",", ":"), ensure_ascii=False).encode()


def json_response(data: Any, status_code: int = 200) -> Response:
    return Response(dumps(data), status_code=status_code, media_type="application/json")
