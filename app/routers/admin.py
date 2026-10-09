from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import BundleVersion, Channel
from ..schemas import (
    BundleDetailOut,
    BundleOut,
    ChannelCreate,
    ChannelOut,
    ChannelUpdate,
    ImportRequest,
    PromoteRequest,
    PublishResponse,
    YankRequest,
)
from ..services import (
    PublishError,
    get_channel,
    get_version,
    promote,
    publish_bundle,
    rollback,
)
from ..storage import version_dir

router = APIRouter(prefix="/v1/admin", tags=["admin"])


def _publish_response(row: BundleVersion, promoted: bool, warnings: list[str]) -> PublishResponse:
    return PublishResponse(
        bundle=BundleOut.model_validate(row), promoted=promoted, warnings=warnings
    )


# --------------------------------------------------------------------------- #
# channels
# --------------------------------------------------------------------------- #

@router.post("/channels", response_model=ChannelOut, status_code=201)
def create_channel(body: ChannelCreate, db: Session = Depends(get_db)):
    if get_channel(db, body.name) is not None:
        raise HTTPException(409, f"channel {body.name!r} already exists")
    ch = Channel(name=body.name, min_app_version=body.min_app_version)
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return ch


@router.get("/channels", response_model=list[ChannelOut])
def list_channels(db: Session = Depends(get_db)):
    return list(db.scalars(select(Channel).order_by(Channel.name)))


@router.patch("/channels/{channel}", response_model=ChannelOut)
def update_channel(channel: str, body: ChannelUpdate, db: Session = Depends(get_db)):
    ch = get_channel(db, channel)
    if ch is None:
        raise HTTPException(404, f"unknown channel {channel!r}")
    if body.min_app_version is not None:
        ch.min_app_version = body.min_app_version
    db.commit()
    db.refresh(ch)
    return ch


@router.post("/channels/{channel}/promote", response_model=ChannelOut)
def promote_version(channel: str, body: PromoteRequest, db: Session = Depends(get_db)):
    ch = get_channel(db, channel)
    if ch is None:
        raise HTTPException(404, f"unknown channel {channel!r}")
    try:
        promote(db, ch, body.version, mandatory=body.mandatory)
    except PublishError as exc:
        raise HTTPException(exc.status_code, exc.message) from None
    db.commit()
    db.refresh(ch)
    return ch


@router.post("/channels/{channel}/rollback", response_model=ChannelOut)
def rollback_channel(channel: str, db: Session = Depends(get_db)):
    ch = get_channel(db, channel)
    if ch is None:
        raise HTTPException(404, f"unknown channel {channel!r}")
    try:
        rollback(db, ch)
    except PublishError as exc:
        raise HTTPException(exc.status_code, exc.message) from None
    db.commit()
    db.refresh(ch)
    return ch


# --------------------------------------------------------------------------- #
# bundles
# --------------------------------------------------------------------------- #

@router.post("/bundles/import", response_model=PublishResponse)
def import_bundle(body: ImportRequest, db: Session = Depends(get_db)):
    """Ingest a bundle that already sits on the server's filesystem — a
    ``bundle_v*.zip`` or an extracted ``_work/v*`` directory."""
    try:
        row, promoted, warnings = publish_bundle(
            db,
            source=Path(body.source_path),
            channel=body.channel,
            quantized=body.quantized,
            notes=body.notes,
            changelog=body.changelog,
            mandatory=body.mandatory,
            promote_after=body.promote,
            allow_unsigned=body.allow_unsigned,
        )
    except PublishError as exc:
        raise HTTPException(exc.status_code, exc.message) from None
    return _publish_response(row, promoted, warnings)


@router.post("/bundles/upload", response_model=PublishResponse)
async def upload_bundle(
    db: Session = Depends(get_db),
    file: UploadFile = File(..., description="a bundle_v*.zip"),
    channel: str = Form(...),
    quantized: bool = Form(False),
    notes: str | None = Form(None),
    changelog: list[str] | None = Form(None, description="repeat the field per line"),
    mandatory: bool = Form(False),
    promote_after: bool = Form(True, alias="promote"),
    allow_unsigned: bool = Form(False),
):
    """Ingest an uploaded ``bundle_v*.zip`` (streamed to a temp file)."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="demo_api_upload_"))
    tmp_zip = tmp_dir / (file.filename or "bundle.zip")
    try:
        with tmp_zip.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        try:
            row, promoted, warnings = publish_bundle(
                db,
                source=tmp_zip,
                channel=channel,
                quantized=quantized,
                notes=notes,
                changelog=changelog,
                mandatory=mandatory,
                promote_after=promote_after,
                allow_unsigned=allow_unsigned,
            )
        except PublishError as exc:
            raise HTTPException(exc.status_code, exc.message) from None
        return _publish_response(row, promoted, warnings)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/bundles", response_model=list[BundleOut])
def list_bundles(db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(BundleVersion).order_by(
                BundleVersion.channel, BundleVersion.released_utc
            )
        )
    )


@router.get("/bundles/{channel}/{version}", response_model=BundleDetailOut)
def get_bundle(channel: str, version: str, db: Session = Depends(get_db)):
    ver = get_version(db, channel, version)
    if ver is None:
        raise HTTPException(404, f"{channel}/{version} not found")
    return ver


@router.post("/bundles/{channel}/{version}/yank", response_model=BundleOut)
def yank_bundle(channel: str, version: str, body: YankRequest, db: Session = Depends(get_db)):
    ver = get_version(db, channel, version)
    if ver is None:
        raise HTTPException(404, f"{channel}/{version} not found")
    ver.yanked = body.yanked
    db.commit()
    db.refresh(ver)
    return ver


@router.delete("/bundles/{channel}/{version}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bundle(channel: str, version: str, db: Session = Depends(get_db)):
    ver = get_version(db, channel, version)
    if ver is None:
        raise HTTPException(404, f"{channel}/{version} not found")
    ch = get_channel(db, channel)
    if ch and version in (ch.latest_version, ch.previous_version):
        raise HTTPException(
            409,
            f"{channel}/{version} is the channel's "
            f"{'latest' if version == ch.latest_version else 'previous'} — repoint it first",
        )
    db.delete(ver)
    db.commit()
    shutil.rmtree(version_dir(channel, version), ignore_errors=True)
    return None
