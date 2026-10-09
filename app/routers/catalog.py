from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..http_utils import etag_json_response
from ..services import build_check, build_index

router = APIRouter(prefix="/v1/models", tags=["catalog"])


@router.get("/index.json", summary="Release index (all channels)")
def index_json(request: Request, db: Session = Depends(get_db)):
    """The app's entry point (METHODOLOGY §1.3). One object per channel with its
    ``latest`` pointer and every non-yanked version."""
    payload = build_index(db)
    return etag_json_response(
        request, payload, max_age=get_settings().index_cache_seconds
    )


@router.get("/check", summary="One-round-trip update descriptor")
def check(
    request: Request,
    channel: str = Query("stable"),
    current: str | None = Query(None, description="the app's currently active bundle_version"),
    app: str | None = Query(None, description="the installed app version"),
    abi: str | None = Query(None, description="device ABI (accepted, unused by this demo)"),
    db: Session = Depends(get_db),
):
    """METHODOLOGY §2.2 — lets the client do one request instead of fetching
    ``index.json`` and diffing."""
    payload = build_check(db, channel=channel, current=current, app_version=app)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"unknown channel {channel!r}")
    return etag_json_response(
        request, payload, max_age=get_settings().index_cache_seconds
    )
