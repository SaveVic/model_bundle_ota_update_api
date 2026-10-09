from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .bundle import BundleConflict, BundleError
from .config import get_settings
from .database import SessionLocal, create_all
from .models import Channel
from .routers import admin, artifacts, catalog


def _seed_channels() -> None:
    settings = get_settings()
    with SessionLocal() as db:
        for name in settings.seed_channels:
            if db.get(Channel, name) is None:
                db.add(Channel(name=name))
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all()
    _seed_channels()
    get_settings().resolved_storage_dir().mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="demo_api — model bundle update server",
    version=__version__,
    summary="OTA distribution of week-1 model bundles (METHODOLOGY.md §2).",
    lifespan=lifespan,
)

app.include_router(catalog.router)
app.include_router(artifacts.router)
app.include_router(admin.router)


@app.exception_handler(BundleError)
async def _bundle_error(_: Request, exc: BundleError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(BundleConflict)
async def _bundle_conflict(_: Request, exc: BundleConflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
def root():
    return {
        "service": "demo_api",
        "version": __version__,
        "docs": "/docs",
        "index": "/v1/models/index.json",
    }


@app.get("/healthz", tags=["meta"])
def healthz():
    return {"status": "ok"}
