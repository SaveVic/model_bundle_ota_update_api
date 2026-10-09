"""Catalog logic shared by the HTTP routers and the seed script."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import bundle as bundlelib
from .config import get_settings
from .models import BundleFile, BundleVersion, Channel
from .storage import rel_storage_path, version_dir
from .versioning import app_supported, is_newer


class PublishError(Exception):
    """Publish failed for a caller-visible reason (bad channel, duplicate, …)."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------- #
# URL construction
# --------------------------------------------------------------------------- #

def bundle_urls(channel: str, version: str) -> dict[str, str]:
    base = f"{get_settings().base_url()}/v1/models/{channel}/{version}"
    return {
        "bundle_zip": f"{base}/bundle.zip",
        "manifest_url": f"{base}/manifest.json",
        "labels_url": f"{base}/labels.json",
        "sha256sums_url": f"{base}/SHA256SUMS",
        "signature_url": f"{base}/SHA256SUMS.sig",
        "files_base": f"{base}/models/",
    }


# --------------------------------------------------------------------------- #
# lookups
# --------------------------------------------------------------------------- #

def get_channel(db: Session, name: str) -> Channel | None:
    return db.get(Channel, name)


def get_version(db: Session, channel: str, version: str) -> BundleVersion | None:
    return db.scalar(
        select(BundleVersion).where(
            BundleVersion.channel == channel, BundleVersion.version == version
        )
    )


def channel_versions(db: Session, channel: str, *, include_yanked: bool = False):
    stmt = select(BundleVersion).where(BundleVersion.channel == channel)
    if not include_yanked:
        stmt = stmt.where(BundleVersion.yanked.is_(False))
    return list(db.scalars(stmt))


# --------------------------------------------------------------------------- #
# index.json  /  check
# --------------------------------------------------------------------------- #

def _version_entry(v: BundleVersion) -> dict:
    urls = bundle_urls(v.channel, v.version)
    return {
        "schema_version": v.schema_version,
        "released_utc": v.released_utc,
        "size_bytes": v.size_bytes,
        "quantized": v.quantized,
        "mandatory": v.mandatory,
        "signature_verified": v.signature_verified,
        "bundle_zip_sha256": v.bundle_zip_sha256,
        "notes": v.notes,
        "changelog": list(v.changelog or []),
        **urls,
    }


def build_index(db: Session) -> dict:
    channels = list(db.scalars(select(Channel)))
    out: dict[str, dict] = {}
    for ch in channels:
        versions = {
            v.version: _version_entry(v)
            for v in sorted(
                channel_versions(db, ch.name), key=lambda x: x.released_utc
            )
        }
        latest = ch.latest_version if ch.latest_version in versions else None
        out[ch.name] = {
            "latest": latest,
            "min_app_version": ch.min_app_version,
            "versions": versions,
        }
    return {"channels": out}


def build_check(
    db: Session,
    *,
    channel: str,
    current: str | None,
    app_version: str | None,
) -> dict | None:
    ch = get_channel(db, channel)
    if ch is None:
        return None

    supported = app_supported(app_version, ch.min_app_version)
    latest_ver = None
    if ch.latest_version:
        latest_ver = get_version(db, channel, ch.latest_version)
        if latest_ver is not None and latest_ver.yanked:
            latest_ver = None

    if latest_ver is None:
        return {
            "update_available": False,
            "channel": channel,
            "latest": None,
            "current": current,
            "min_app_version": ch.min_app_version,
            "app_supported": supported,
        }

    available = supported and is_newer(latest_ver.version, current)
    payload = {
        "update_available": available,
        "channel": channel,
        "latest": latest_ver.version,
        "current": current,
        "mandatory": latest_ver.mandatory,
        "min_app_version": ch.min_app_version,
        "app_supported": supported,
        "schema_version": latest_ver.schema_version,
        "size_bytes": latest_ver.size_bytes,
        "quantized": latest_ver.quantized,
        "signature_verified": latest_ver.signature_verified,
        "bundle_zip_sha256": latest_ver.bundle_zip_sha256,
        "release_notes": latest_ver.notes,
        "changelog": list(latest_ver.changelog or []),
        **bundle_urls(channel, latest_ver.version),
    }
    return payload


# --------------------------------------------------------------------------- #
# promote / rollback
# --------------------------------------------------------------------------- #

def promote(db: Session, ch: Channel, version: str, *, mandatory: bool | None = None) -> None:
    ver = get_version(db, ch.name, version)
    if ver is None:
        raise PublishError(f"{ch.name}/{version} does not exist", 404)
    if ver.yanked:
        raise PublishError(f"{ch.name}/{version} is yanked", 409)
    if mandatory is not None:
        ver.mandatory = mandatory
    if ch.latest_version != version:
        ch.previous_version = ch.latest_version
        ch.latest_version = version
    db.flush()


def rollback(db: Session, ch: Channel) -> str:
    if not ch.previous_version:
        raise PublishError(f"{ch.name} has no previous version to roll back to", 409)
    target = ch.previous_version
    ch.previous_version = ch.latest_version
    ch.latest_version = target
    db.flush()
    return target


# --------------------------------------------------------------------------- #
# publish
# --------------------------------------------------------------------------- #

def publish_bundle(
    db: Session,
    *,
    source: Path,
    channel: str,
    quantized: bool = False,
    notes: str | None = None,
    changelog: list[str] | None = None,
    mandatory: bool = False,
    promote_after: bool = True,
    allow_unsigned: bool = False,
) -> tuple[BundleVersion, bool, list[str]]:
    settings = get_settings()
    ch = get_channel(db, channel)
    if ch is None:
        raise PublishError(f"unknown channel {channel!r} — create it first", 404)

    result = bundlelib.ingest(source, allow_unsigned=allow_unsigned)
    dest = version_dir(channel, result.bundle_version)
    moved = False
    try:
        if get_version(db, channel, result.bundle_version) is not None or dest.exists():
            raise PublishError(
                f"{channel}/{result.bundle_version} already exists (bundles are immutable)",
                409,
            )

        result.move_to(dest)
        moved = True

        row = BundleVersion(
            channel=channel,
            version=result.bundle_version,
            schema_version=result.schema_version,
            created_utc=result.created_utc,
            source_commit=result.source_commit,
            quantized=quantized,
            mandatory=mandatory,
            size_bytes=result.size_bytes,
            bundle_zip_sha256=result.bundle_zip_sha256,
            manifest_sha256=result.manifest_sha256,
            signature_verified=result.signature_verified,
            notes=notes,
            changelog=list(changelog or []),
            storage_path=rel_storage_path(channel, result.bundle_version),
        )
        db.add(row)
        db.flush()
        for fe in result.files:
            db.add(
                BundleFile(
                    version_id=row.id,
                    relpath=fe.relpath,
                    sha256=fe.sha256,
                    size_bytes=fe.size_bytes,
                )
            )

        promoted = False
        if promote_after and (
            settings.auto_promote_on_publish or ch.latest_version is None
        ):
            promote(db, ch, result.bundle_version, mandatory=mandatory or None)
            promoted = True

        db.commit()
        db.refresh(row)
        return row, promoted, result.warnings
    except Exception:
        db.rollback()
        # only undo files this call actually staged — never touch a bundle that
        # was already on disk (e.g. a duplicate-publish attempt).
        if moved and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        result.cleanup()
