from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import Response


def _default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        s = obj.isoformat()
        return s if obj.tzinfo is not None else s + "Z"
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"not JSON-serializable: {type(obj)!r}")


def canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload, default=_default, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def etag_json_response(
    request: Request,
    payload: Any,
    *,
    max_age: int,
) -> Response:
    """Serve ``payload`` as JSON with a strong ``ETag`` and ``Cache-Control``.
    Returns ``304`` when the client's ``If-None-Match`` already matches."""
    body = canonical_json(payload)
    etag = '"' + hashlib.sha256(body).hexdigest() + '"'
    cache = f"public, max-age={max_age}"

    inm = request.headers.get("if-none-match", "")
    if etag in [t.strip() for t in inm.split(",")]:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache})

    return Response(
        content=body,
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": cache},
    )
