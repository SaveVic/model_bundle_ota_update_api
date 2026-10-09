"""Seed the catalog from the bundles already built under ``week-1/pipeline-app``.

    python -m scripts.seed                 # quant bundle -> stable (promoted)
    python -m scripts.seed --with-unquant  # also unquant   -> beta

Run from the ``week-3/demo_api`` directory (after installing requirements).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import APP_ROOT, get_settings
from app.database import SessionLocal, create_all
from app.http_utils import canonical_json
from app.models import Channel
from app.services import build_index, publish_bundle, PublishError

DEFAULT_DIST = (
    APP_ROOT / ".." / ".." / "week-1" / "pipeline-app" / "build" / "dist"
).resolve()


def _ensure_channel(db, name: str) -> None:
    if db.get(Channel, name) is None:
        db.add(Channel(name=name))
        db.commit()


def _publish(
    source: Path, channel: str, *, quantized: bool, notes: str, changelog: list[str]
) -> None:
    with SessionLocal() as db:
        _ensure_channel(db, channel)
        try:
            row, promoted, warnings = publish_bundle(
                db,
                source=source,
                channel=channel,
                quantized=quantized,
                notes=notes,
                changelog=changelog,
                promote_after=True,
                allow_unsigned=True,  # seed is best-effort; sig may be absent locally
            )
        except PublishError as exc:
            print(f"  ! {channel}: {exc.message}")
            return
        flag = " (promoted)" if promoted else ""
        print(
            f"  + {channel}/{row.version}{flag}  sig_verified={row.signature_verified}"
        )
        for w in warnings:
            print(f"    warning: {w}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST)
    ap.add_argument("--quant-zip", default="bundle_v1_quant.zip")
    ap.add_argument("--unquant-zip", default="bundle_v1.zip")
    ap.add_argument(
        "--with-unquant",
        action="store_true",
        help="also publish the unquantized bundle to beta",
    )
    args = ap.parse_args()

    create_all()
    print(f"storage: {get_settings().resolved_storage_dir()}")
    print(f"dist:    {args.dist_dir}")

    quant = args.dist_dir / args.quant_zip
    if quant.is_file():
        _publish(
            quant,
            "beta",
            quantized=True,
            notes="seeded quantized bundle",
            changelog=[
                "Initial field bundle",
                "seg_lung fp16, cnn / seg_lesion int8",
                "RF: dataset_gabungan active",
            ],
        )
    else:
        print(f"  ! skipped beta: {quant} not found")

    if args.with_unquant:
        unquant = args.dist_dir / args.unquant_zip
        if unquant.is_file():
            _publish(
                unquant,
                "stable",
                quantized=False,
                notes="seeded unquantized bundle",
                changelog=["Unquantized parity build", "full-precision seg + cnn"],
            )
        else:
            print(f"  ! skipped stable: {unquant} not found")

    with SessionLocal() as db:
        print()
        print(canonical_json(build_index(db)).decode("utf-8"))


if __name__ == "__main__":
    main()
