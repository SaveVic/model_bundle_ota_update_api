from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import get_version
from ..storage import safe_member_path, version_dir

router = APIRouter(prefix="/v1/models", tags=["artifacts"])

_IMMUTABLE = "public, max-age=31536000, immutable"
_GET_HEAD = ["GET", "HEAD"]


def _resolve_dir(db: Session, channel: str, version: str) -> Path:
    ver = get_version(db, channel, version)
    if ver is None or ver.yanked:
        raise HTTPException(status_code=404, detail=f"{channel}/{version} not found")
    d = version_dir(channel, version)
    if not d.is_dir():
        raise HTTPException(status_code=404, detail="bundle files are missing on disk")
    return d


def _file(path: Path, media_type: str, filename: str, *, as_attachment: bool) -> FileResponse:
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    kwargs = {"filename": filename} if as_attachment else {}
    # Starlette FileResponse handles Range, ETag, Last-Modified and HEAD.
    return FileResponse(
        path, media_type=media_type, headers={"Cache-Control": _IMMUTABLE}, **kwargs
    )


@router.api_route("/{channel}/{version}/manifest.json", methods=_GET_HEAD)
def get_manifest(channel: str, version: str, db: Session = Depends(get_db)):
    return _file(_resolve_dir(db, channel, version) / "manifest.json", "application/json", "manifest.json", as_attachment=False)


@router.api_route("/{channel}/{version}/labels.json", methods=_GET_HEAD)
def get_labels(channel: str, version: str, db: Session = Depends(get_db)):
    return _file(_resolve_dir(db, channel, version) / "labels.json", "application/json", "labels.json", as_attachment=False)


@router.api_route("/{channel}/{version}/SHA256SUMS", methods=_GET_HEAD)
def get_sha256sums(channel: str, version: str, db: Session = Depends(get_db)):
    return _file(_resolve_dir(db, channel, version) / "SHA256SUMS", "text/plain; charset=utf-8", "SHA256SUMS", as_attachment=False)


@router.api_route("/{channel}/{version}/SHA256SUMS.sig", methods=_GET_HEAD)
def get_signature(channel: str, version: str, db: Session = Depends(get_db)):
    return _file(_resolve_dir(db, channel, version) / "SHA256SUMS.sig", "application/octet-stream", "SHA256SUMS.sig", as_attachment=True)


@router.api_route("/{channel}/{version}/bundle.zip", methods=_GET_HEAD)
def get_bundle_zip(channel: str, version: str, db: Session = Depends(get_db)):
    """The whole bundle, one file. Supports ``Range`` for resumable download."""
    return _file(_resolve_dir(db, channel, version) / "bundle.zip", "application/zip", "bundle.zip", as_attachment=True)


@router.api_route("/{channel}/{version}/models/{file:path}", methods=_GET_HEAD)
def get_model_file(channel: str, version: str, file: str, db: Session = Depends(get_db)):
    """One model file, for file-by-file / delta download keyed by
    ``manifest.file_digests_sha256``. Supports ``Range``."""
    models_dir = _resolve_dir(db, channel, version) / "models"
    try:
        target = safe_member_path(models_dir, file)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad file path") from None
    return _file(target, "application/octet-stream", target.name, as_attachment=True)
