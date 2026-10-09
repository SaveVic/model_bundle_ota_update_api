from __future__ import annotations

from pathlib import Path, PurePosixPath

from .config import get_settings


def storage_root() -> Path:
    root = get_settings().resolved_storage_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def rel_storage_path(channel: str, version: str) -> str:
    """The DB-stored path for a version, relative to the storage root."""
    return f"models/{channel}/{version}"


def version_dir(channel: str, version: str) -> Path:
    return storage_root() / "models" / channel / version


def safe_member_path(base: Path, relative: str) -> Path:
    """Resolve ``relative`` (a client-supplied ``models/{file:path}`` segment)
    under ``base``, refusing traversal, absolute paths and drive letters.

    Raises ``ValueError`` on anything suspicious.
    """
    rel = relative.replace("\\", "/").strip("/")
    pp = PurePosixPath(rel)
    if pp.is_absolute() or any(part in ("..", "") for part in pp.parts):
        raise ValueError(f"unsafe path: {relative!r}")
    if ":" in rel:  # windows drive / ADS
        raise ValueError(f"unsafe path: {relative!r}")

    resolved = (base / rel).resolve()
    base_resolved = base.resolve()
    if base_resolved != resolved and base_resolved not in resolved.parents:
        raise ValueError(f"path escapes bundle dir: {relative!r}")
    return resolved
