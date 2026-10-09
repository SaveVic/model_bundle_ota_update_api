"""Bundle ingest + verification.

A *bundle* is the tree described in ``METHODOLOGY.md`` §1.1:

    manifest.json  labels.json  SHA256SUMS  SHA256SUMS.sig  models/*

This module takes a local ``.zip`` or an already-extracted directory, verifies it
end to end (every file digest against ``SHA256SUMS``; the manifest's mirrored
``models/*`` digests; the Ed25519 signature over ``SHA256SUMS`` against the pinned
public key), and produces a canonical ``bundle.zip`` for one-shot download.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

_META_FILES = ("manifest.json", "labels.json")
_SUMS = "SHA256SUMS"
_SIG = "SHA256SUMS.sig"
_MANIFEST_REQUIRED = ("schema_version", "bundle_version", "created_utc", "file_digests_sha256")


class BundleError(ValueError):
    """A bundle failed validation (maps to HTTP 422)."""


class BundleConflict(Exception):
    """This (channel, version) already exists (maps to HTTP 409)."""


@dataclass
class FileEntry:
    relpath: str  # posix, relative to bundle root
    sha256: str
    size_bytes: int


@dataclass
class IngestResult:
    workdir: Path  # owned temp dir; call cleanup()
    root: Path  # the bundle root inside workdir (holds manifest.json + bundle.zip)
    manifest: dict
    schema_version: int
    bundle_version: str
    created_utc: str
    source_commit: str | None
    files: list[FileEntry]
    manifest_sha256: str
    bundle_zip_path: Path
    bundle_zip_sha256: str
    size_bytes: int
    signature_present: bool
    signature_verified: bool
    warnings: list[str] = field(default_factory=list)

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def move_to(self, dest: Path) -> None:
        """Move the verified bundle tree (including the generated ``bundle.zip``)
        to ``dest``. ``dest`` must not already exist."""
        if dest.exists():
            raise BundleConflict(f"{dest} already exists")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self.root), str(dest))


# --------------------------------------------------------------------------- #
# hashing helpers
# --------------------------------------------------------------------------- #

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# SHA256SUMS parsing
# --------------------------------------------------------------------------- #

def parse_sha256sums(text: str) -> dict[str, str]:
    """``<64 hex><2 spaces><posix path>`` per line. Tolerant of LF or CRLF and of
    the ``sha256sum`` binary-mode ``*`` marker."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # split on runs of whitespace, but the path may contain spaces -> split once
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise BundleError(f"malformed SHA256SUMS line: {raw!r}")
        digest, name = parts[0].lower(), parts[1].strip()
        if name.startswith("*"):
            name = name[1:]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise BundleError(f"malformed digest in SHA256SUMS: {raw!r}")
        out[name.replace("\\", "/")] = digest
    if not out:
        raise BundleError("SHA256SUMS is empty")
    return out


# --------------------------------------------------------------------------- #
# safe zip extraction
# --------------------------------------------------------------------------- #

def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in zf.infolist():
        name = member.filename.replace("\\", "/")
        if name.endswith("/"):
            continue
        target = (dest / name).resolve()
        if dest != target and dest not in target.parents:
            raise BundleError(f"zip entry escapes archive root: {member.filename!r}")
    zf.extractall(dest)


def _find_bundle_root(tree: Path) -> Path:
    """Return the directory that directly contains ``manifest.json`` — either
    ``tree`` itself or a single wrapper directory one level down."""
    if (tree / "manifest.json").is_file():
        return tree
    subdirs = [p for p in tree.iterdir() if p.is_dir()]
    for sub in subdirs:
        if (sub / "manifest.json").is_file():
            return sub
    raise BundleError("no manifest.json found at the bundle root or one level down")


# --------------------------------------------------------------------------- #
# signature
# --------------------------------------------------------------------------- #

def _load_pinned_pubkey(pubkey_path: Path | None) -> Ed25519PublicKey | None:
    if pubkey_path is None:
        from .config import get_settings

        pubkey_path = get_settings().resolved_pubkey_path()
    if not pubkey_path.is_file():
        return None
    key = load_pem_public_key(pubkey_path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise BundleError(f"pinned key at {pubkey_path} is not an Ed25519 public key")
    return key


def verify_signature(
    sums_bytes: bytes, sig_bytes: bytes, pubkey_path: Path | None = None
) -> bool:
    """Ed25519-verify the detached signature over the *raw* bytes of
    ``SHA256SUMS`` (CRLF included — never normalize). Returns False on any
    failure, including a missing pinned key."""
    key = _load_pinned_pubkey(pubkey_path)
    if key is None:
        return False
    if len(sig_bytes) != 64:
        return False
    try:
        key.verify(sig_bytes, sums_bytes)
        return True
    except InvalidSignature:
        return False


# --------------------------------------------------------------------------- #
# canonical bundle.zip
# --------------------------------------------------------------------------- #

def _build_bundle_zip(root: Path, out: Path) -> None:
    """Zip the bundle tree with entries at the archive root (no wrapper dir),
    deterministic order. Excludes any pre-existing ``bundle.zip``."""
    members: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == "bundle.zip":
            continue
        members.append((path, rel))
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, rel in members:
            zf.write(path, rel)


# --------------------------------------------------------------------------- #
# the pipeline
# --------------------------------------------------------------------------- #

def ingest(
    source: Path,
    *,
    allow_unsigned: bool = False,
) -> IngestResult:
    """Verify a bundle from ``source`` (a ``.zip`` file or an extracted dir).

    On success the returned :class:`IngestResult` owns a temp directory holding
    the verified tree plus a freshly built ``bundle.zip``; the caller persists it
    with ``result.move_to(dest)`` and must ``result.cleanup()`` in a ``finally``.
    """
    from .config import get_settings

    settings = get_settings()
    source = source.expanduser()
    if not source.exists():
        raise BundleError(f"source does not exist: {source}")

    workdir = Path(tempfile.mkdtemp(prefix="demo_api_ingest_"))
    try:
        staged = workdir / "staged"
        staged.mkdir()

        if source.is_file():
            if source.suffix.lower() != ".zip":
                raise BundleError(f"source file is not a .zip: {source.name}")
            with zipfile.ZipFile(source) as zf:
                _safe_extract(zf, staged)
        elif source.is_dir():
            shutil.copytree(source, staged, dirs_exist_ok=True)
        else:
            raise BundleError(f"source is neither a file nor a directory: {source}")

        root = _find_bundle_root(staged)

        # If the root sat one level down, hoist it so bundle.zip / move_to are clean.
        if root != staged:
            hoisted = workdir / "bundle"
            shutil.move(str(root), str(hoisted))
            root = hoisted

        return _verify_tree(root, workdir, settings.require_signature, allow_unsigned)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def _verify_tree(
    root: Path,
    workdir: Path,
    require_signature: bool,
    allow_unsigned: bool,
) -> IngestResult:
    manifest_path = root / "manifest.json"
    sums_path = root / _SUMS
    sig_path = root / _SIG

    if not sums_path.is_file():
        raise BundleError("bundle has no SHA256SUMS")

    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise BundleError(f"manifest.json is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise BundleError("manifest.json is not a JSON object")
    for key in _MANIFEST_REQUIRED:
        if key not in manifest:
            raise BundleError(f'manifest.json is missing "{key}"')

    schema_version = manifest["schema_version"]
    if not isinstance(schema_version, int):
        raise BundleError("manifest.schema_version must be an integer")
    bundle_version = str(manifest["bundle_version"]).strip()
    if not bundle_version:
        raise BundleError("manifest.bundle_version is empty")

    sums_bytes = sums_path.read_bytes()
    sums = parse_sha256sums(sums_bytes.decode("utf-8"))

    warnings: list[str] = []
    files: list[FileEntry] = []

    # 1. every SHA256SUMS entry: file present + digest matches
    for rel, expected in sorted(sums.items()):
        if rel in (_SUMS, _SIG):
            continue
        fpath = root / rel
        if not fpath.is_file():
            raise BundleError(f"SHA256SUMS lists {rel!r} but it is not in the bundle")
        actual = sha256_file(fpath)
        if actual != expected:
            raise BundleError(
                f"digest mismatch for {rel}: SHA256SUMS says {expected}, file is {actual}"
            )
        files.append(FileEntry(rel, actual, fpath.stat().st_size))

    covered = {f.relpath for f in files}
    for meta in _META_FILES:
        if (root / meta).is_file() and meta not in covered:
            warnings.append(f"{meta} present but not listed in SHA256SUMS")

    # 2. manifest's mirrored models/* digests must agree with SHA256SUMS
    md = manifest.get("file_digests_sha256")
    if not isinstance(md, dict) or not md:
        raise BundleError("manifest.file_digests_sha256 is missing or empty")
    for rel, expected in md.items():
        rel_norm = str(rel).replace("\\", "/")
        if rel_norm not in sums:
            raise BundleError(
                f"manifest.file_digests_sha256 references {rel_norm!r}, absent from SHA256SUMS"
            )
        if str(expected).lower() != sums[rel_norm]:
            raise BundleError(
                f"manifest vs SHA256SUMS digest disagree for {rel_norm}"
            )

    # 3. every pipeline step's model file must exist
    for step in manifest.get("pipeline", []):
        model = step.get("model")
        if not model or model == "@rf_active":
            continue
        if not (root / "models" / model).is_file():
            raise BundleError(f"pipeline step {step.get('id')!r} needs models/{model}, missing")

    # 4. Ed25519 signature over the raw SHA256SUMS bytes
    signature_present = sig_path.is_file()
    signature_verified = False
    if signature_present:
        signature_verified = verify_signature(sums_bytes, sig_path.read_bytes())

    if not signature_verified:
        reason = "SHA256SUMS.sig missing" if not signature_present else "signature did not verify against the pinned key"
        if require_signature and not allow_unsigned:
            raise BundleError(
                f"{reason}; publish with allow_unsigned=true or set DEMO_API_REQUIRE_SIGNATURE=false"
            )
        warnings.append(f"{reason}; accepted with signature_verified=false")

    # 5. build the canonical one-shot bundle.zip
    bundle_zip = root / "bundle.zip"
    _build_bundle_zip(root, bundle_zip)
    bundle_zip_sha256 = sha256_file(bundle_zip)
    size_bytes = bundle_zip.stat().st_size
    files.append(FileEntry("bundle.zip", bundle_zip_sha256, size_bytes))

    return IngestResult(
        workdir=workdir,
        root=root,
        manifest=manifest,
        schema_version=schema_version,
        bundle_version=bundle_version,
        created_utc=str(manifest.get("created_utc", "")),
        source_commit=(str(manifest["source_commit"]) if manifest.get("source_commit") else None),
        files=files,
        manifest_sha256=sha256_bytes(manifest_bytes),
        bundle_zip_path=bundle_zip,
        bundle_zip_sha256=bundle_zip_sha256,
        size_bytes=size_bytes,
        signature_present=signature_present,
        signature_verified=signature_verified,
        warnings=warnings,
    )
