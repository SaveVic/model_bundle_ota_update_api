from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# release index  (GET /v1/models/index.json)  — METHODOLOGY §1.3
# --------------------------------------------------------------------------- #

class IndexVersionEntry(BaseModel):
    schema_version: int
    released_utc: datetime
    size_bytes: int
    quantized: bool
    mandatory: bool
    bundle_zip: str
    bundle_zip_sha256: str
    manifest_url: str
    labels_url: str
    sha256sums_url: str
    signature_url: str
    files_base: str
    signature_verified: bool
    notes: str | None = None
    changelog: list[str] = []


class IndexChannelEntry(BaseModel):
    latest: str | None
    min_app_version: str
    versions: dict[str, IndexVersionEntry]


class IndexResponse(BaseModel):
    channels: dict[str, IndexChannelEntry]


# --------------------------------------------------------------------------- #
# check descriptor  (GET /v1/models/check)  — METHODOLOGY §2.2
# --------------------------------------------------------------------------- #

class CheckResponse(BaseModel):
    update_available: bool
    channel: str
    latest: str | None
    current: str | None = None
    mandatory: bool = False
    min_app_version: str | None = None
    app_supported: bool = True
    schema_version: int | None = None
    size_bytes: int | None = None
    quantized: bool | None = None
    signature_verified: bool | None = None
    bundle_zip: str | None = None
    bundle_zip_sha256: str | None = None
    manifest_url: str | None = None
    labels_url: str | None = None
    sha256sums_url: str | None = None
    signature_url: str | None = None
    files_base: str | None = None
    release_notes: str | None = None
    changelog: list[str] = []


# --------------------------------------------------------------------------- #
# admin
# --------------------------------------------------------------------------- #

class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    min_app_version: str = "0.0.0"


class ChannelUpdate(BaseModel):
    min_app_version: str | None = None


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    min_app_version: str
    latest_version: str | None
    previous_version: str | None
    updated_at: datetime


class ImportRequest(BaseModel):
    source_path: str = Field(description="local path to a bundle_v*.zip or an extracted bundle dir")
    channel: str
    quantized: bool = False
    notes: str | None = None
    changelog: list[str] = []
    mandatory: bool = False
    promote: bool = True
    allow_unsigned: bool = False


class PromoteRequest(BaseModel):
    version: str
    mandatory: bool | None = None


class YankRequest(BaseModel):
    yanked: bool = True


class BundleFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    relpath: str
    sha256: str
    size_bytes: int


class BundleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    channel: str
    version: str
    schema_version: int
    created_utc: str
    source_commit: str | None
    quantized: bool
    mandatory: bool
    yanked: bool
    size_bytes: int
    bundle_zip_sha256: str
    manifest_sha256: str
    signature_verified: bool
    notes: str | None
    changelog: list[str] = []
    released_utc: datetime


class BundleDetailOut(BundleOut):
    files: list[BundleFileOut]


class PublishResponse(BaseModel):
    bundle: BundleOut
    promoted: bool
    warnings: list[str] = []
