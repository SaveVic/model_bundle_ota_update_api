from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..http_utils import etag_json_response
from ..schemas import AvailableModelItem, AvailableModelsResponse
from ..services import (
    build_check,
    build_index,
    get_bundle_descriptor,
    list_available_models,
)

router = APIRouter(prefix="/v1/models", tags=["catalog"])


@router.get(
    "",
    summary="List all available model bundles across channels",
    response_model=AvailableModelsResponse,
)
@router.get(
    "/available",
    summary="List all available model bundles (alias)",
    response_model=AvailableModelsResponse,
    include_in_schema=False,
)
def list_models(
    request: Request,
    channel: str | None = Query(None, description="filter by channel name (e.g. stable, beta)"),
    quantized: bool | None = Query(None, description="filter by quantization status"),
    app: str | None = Query(None, description="the installed app version to test compatibility"),
    current: str | None = Query(None, description="the app's currently active bundle_version"),
    db: Session = Depends(get_db),
):
    """Returns a flat, structured list of all non-yanked model bundles across
    all or filtered channels. Allows edge clients to browse, filter, and choose
    which bundle version to download rather than only taking latest."""
    payload = list_available_models(
        db,
        channel=channel,
        quantized=quantized,
        app_version=app,
        current=current,
    )
    return etag_json_response(
        request, payload, max_age=get_settings().index_cache_seconds
    )


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


@router.get(
    "/{channel}/{version}",
    summary="Get descriptor for a specific bundle version",
    response_model=AvailableModelItem,
)
def get_bundle_info(
    request: Request,
    channel: str,
    version: str,
    app: str | None = Query(None, description="the installed app version to test compatibility"),
    current: str | None = Query(None, description="the app's currently active bundle_version"),
    db: Session = Depends(get_db),
):
    """Returns the full client descriptor for a specific chosen bundle version.
    Allows clients to inspect and verify metadata before downloading a non-latest
    or alternate channel bundle."""
    payload = get_bundle_descriptor(
        db,
        channel=channel,
        version=version,
        app_version=app,
        current=current,
    )
    if payload is None:
        raise HTTPException(
            status_code=404, detail=f"bundle {channel}/{version} not found"
        )
    return etag_json_response(
        request, payload, max_age=get_settings().index_cache_seconds
    )

