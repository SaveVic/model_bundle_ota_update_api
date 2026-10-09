from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Channel(Base):
    """A release track (``stable``, ``beta``, …). Holds exactly one ``latest``
    pointer (the ACTIVE version for that channel) and keeps the prior one for a
    single-step rollback."""

    __tablename__ = "channels"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    min_app_version: Mapped[str] = mapped_column(String(32), default="0.0.0")
    latest_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    versions: Mapped[list["BundleVersion"]] = relationship(
        back_populates="channel_ref",
        cascade="all, delete-orphan",
    )


class BundleVersion(Base):
    """One immutable, verified bundle published to one channel."""

    __tablename__ = "bundle_versions"
    __table_args__ = (UniqueConstraint("channel", "version", name="uq_channel_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(
        String(64), ForeignKey("channels.name", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(64), index=True)  # manifest.bundle_version

    schema_version: Mapped[int] = mapped_column(Integer)
    created_utc: Mapped[str] = mapped_column(String(40), default="")
    source_commit: Mapped[str | None] = mapped_column(String(128), nullable=True)

    quantized: Mapped[bool] = mapped_column(Boolean, default=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, default=False)
    yanked: Mapped[bool] = mapped_column(Boolean, default=False)

    size_bytes: Mapped[int] = mapped_column(Integer, default=0)  # bundle.zip
    bundle_zip_sha256: Mapped[str] = mapped_column(String(64), default="")
    manifest_sha256: Mapped[str] = mapped_column(String(64), default="")
    signature_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    changelog: Mapped[list[str]] = mapped_column(JSON, default=list)
    storage_path: Mapped[str] = mapped_column(String(255))  # rel to storage_dir

    released_utc: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    channel_ref: Mapped[Channel] = relationship(back_populates="versions")
    files: Mapped[list["BundleFile"]] = relationship(
        back_populates="version_ref",
        cascade="all, delete-orphan",
    )


class BundleFile(Base):
    """Per-file digest for one bundle version — mirrors ``SHA256SUMS`` and lets a
    client verify a single lazily-downloaded model file."""

    __tablename__ = "bundle_files"
    __table_args__ = (
        UniqueConstraint("version_id", "relpath", name="uq_version_relpath"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bundle_versions.id", ondelete="CASCADE"), index=True
    )
    relpath: Mapped[str] = mapped_column(String(255))  # posix, e.g. models/seg_lung.onnx
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)

    version_ref: Mapped[BundleVersion] = relationship(back_populates="files")


__all__ = ["Channel", "BundleVersion", "BundleFile"]
