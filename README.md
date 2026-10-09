# Model Bundle OTA Update Server

Over-The-Air (OTA) distribution and release management server for machine learning model bundles built by the TBScreen edge pipeline. Implements the model update specification for edge diagnostic platforms.

> [!NOTE]
> For comprehensive architectural diagrams, bundle file specifications, cryptographic security models, and client integration protocols, see the full technical guide in [**DOCS.md**](DOCS.md).

---

## What It Is & Why It Exists

In clinical screening applications, machine learning models run locally on edge hardware (mobile devices running Flutter / ONNX Runtime). Upgrading neural network weights, calibrating classification thresholds, or reconfiguring execution graphs would normally require recompiling and distributing a new mobile application binary (APK).

`demo_api` eliminates this operational bottleneck:
- **Zero-APK Model Releases:** Pushes new models, segmentation weights, and pipeline wiring directly to client devices without app store delays.
- **Asymmetric Signature Gating:** Enforces Ed25519 digital signature validation at ingestion time against a pinned public key (`keys/bundle_signing_key_pub.pem`).
- **One-Round-Trip Update Checking:** Provides `/v1/models/check` to evaluate client versions, application compatibility floors (`min_app_version`), and mandatory update flags in a single HTTP request.
- **Resumable & Cached Distribution:** Serves large model binaries (`bundle.zip` and individual `.onnx` files) with HTTP `Range` byte-serving, strong `ETag` generation, and immutable cache headers.
- **Multi-Track Release Channels:** Supports independent distribution tracks (e.g., `stable`, `beta`) with atomic zero-downtime promotions and single-step rollbacks.

---

## Tech Stack

- **Framework:** FastAPI (Python 3.10+)
- **ASGI Server:** Uvicorn
- **ORM & Database:** SQLAlchemy 2.0 (mapped column syntax) · SQLite (`demo_api.db`)
- **Data Validation & Settings:** Pydantic v2 · Pydantic Settings
- **Cryptography:** `cryptography` (Ed25519 signature verification)
- **Versioning:** `packaging` (PEP 440 semantic version parsing and comparison)

---

## Architecture Overview

```
                      +---------------------------------------+
                      |         Pipeline Build Machine        |
                      |  build_bundle.py  ->  sign.py (Ed25519)|
                      +---------------------------------------+
                                          |
                                          v
+---------------------------------------------------------------------------------+
|                                demo_api Server                                  |
|                                                                                 |
|  +--------------------+    +----------------------+    +---------------------+  |
|  |   Ingest Engine    |    |     Catalog Store    |    |    File Storage     |  |
|  |  (app/bundle.py)   |    |    (SQLite via ORM)  |    | (storage/models/..) |  |
|  |  - Unpack & Verify |    |  - Channels          |    |  - manifest.json    |  |
|  |  - SHA256 Cascade  |--->|  - BundleVersion     |--->|  - SHA256SUMS(.sig) |  |
|  |  - Ed25519 Sig     |    |  - BundleFile        |    |  - models/*.onnx    |  |
|  |  - Build bundle.zip|    |                      |    |  - bundle.zip       |  |
|  +--------------------+    +----------------------+    +---------------------+  |
|                                       ^                           ^             |
|                                       |                           |             |
|  +------------------------------------+---------------------------+----------+  |
|  | HTTP Routers: catalog.py (/check, index.json) | artifacts.py | admin.py   |  |
+---------------------------------------------------------------------------------+
                                        ^
                                        | (HTTP / Range / ETag)
                      +---------------------------------------+
                      |         Edge Diagnostic App           |
                      |  (Flutter / Android / ONNX Runtime)   |
                      +---------------------------------------+
```

---

## Quick Start

### 1. Prerequisites & Virtual Environment

From the project root:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configuration (Optional)

All settings have working defaults for local execution. To customize, copy the example environment configuration:

```powershell
copy .env.example .env
```

Key environment options (configured via `DEMO_API_*`):
- `DEMO_API_DATABASE_URL`: Database connection string (default: `sqlite:///./demo_api.db`).
- `DEMO_API_STORAGE_DIR`: Extracted bundle directory (default: `storage`).
- `DEMO_API_PUBLIC_BASE_URL`: Base URL for links returned in descriptors (e.g. `http://localhost:8000` or public domain).
- `DEMO_API_REQUIRE_SIGNATURE`: Whether invalid Ed25519 signatures block ingestion (default: `true`).

### 3. Run the Server

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- **Interactive API Documentation:** <http://localhost:8000/docs>
- **Health Check:** `GET http://localhost:8000/healthz`
- **Release Index:** `GET http://localhost:8000/v1/models/index.json`

On initial startup, SQLite automatically creates the schema and seeds default channels (`stable` and `beta`).

---

## Seeding Models

To seed the catalog with sample model bundle artifacts:

```powershell
# Ingest and promote quantized bundle into the 'stable' channel
python -m scripts.seed

# Ingest quantized bundle into 'beta' and unquantized bundle into 'stable'
python -m scripts.seed --with-unquant
```

---

## HTTP API Surface

### Client Endpoints (Read-Only)

| Method | Endpoint | Cache Policy | Description |
|---|---|---|---|
| `GET` | `/v1/models/index.json` | `Cache-Control: 60s` · `ETag` | Full release index across all channels. |
| `GET` | `/v1/models/check` | `Cache-Control: 60s` · `ETag` | Single-query update descriptor (`?channel=&current=&app=`). |
| `GET` / `HEAD` | `/v1/models/{ch}/{ver}/manifest.json` | `max-age=31536000, immutable` | Pipeline execution graph and tensor wiring. |
| `GET` / `HEAD` | `/v1/models/{ch}/{ver}/labels.json` | `max-age=31536000, immutable` | UI labels, lesion classes, and color palette. |
| `GET` / `HEAD` | `/v1/models/{ch}/{ver}/SHA256SUMS` | `max-age=31536000, immutable` | POSIX checksum manifest covering all bundled files. |
| `GET` / `HEAD` | `/v1/models/{ch}/{ver}/SHA256SUMS.sig` | `max-age=31536000, immutable` | Detached 64-byte Ed25519 signature. |
| `GET` / `HEAD` | `/v1/models/{ch}/{ver}/bundle.zip` | `max-age=31536000, immutable` | Complete archive. Supports HTTP `Range` (resumable). |
| `GET` / `HEAD` | `/v1/models/{ch}/{ver}/models/{file}` | `max-age=31536000, immutable` | Single ONNX model. Supports HTTP `Range`. |

### Administrative Endpoints (No Auth in Demo)

| Method | Endpoint | Request Body | Description |
|---|---|---|---|
| `POST` | `/v1/admin/channels` | `{"name": "...", "min_app_version": "..."}` | Register a new release track. |
| `GET` | `/v1/admin/channels` | — | List all channels and active version pointers. |
| `PATCH` | `/v1/admin/channels/{ch}` | `{"min_app_version": "..."}` | Update application compatibility floor. |
| `POST` | `/v1/admin/channels/{ch}/promote` | `{"version": "...", "mandatory": false}` | Promote a specific version to `latest`. |
| `POST` | `/v1/admin/channels/{ch}/rollback` | — | Atomic single-step rollback (`latest <- previous`). |
| `POST` | `/v1/admin/bundles/import` | JSON (`source_path`, `channel`, ...) | Ingest bundle from local server path. |
| `POST` | `/v1/admin/bundles/upload` | Multipart form (`file=bundle.zip`, ...) | Upload and ingest a bundle archive. |
| `GET` | `/v1/admin/bundles` | — | List all published bundles across all channels. |
| `GET` | `/v1/admin/bundles/{ch}/{ver}` | — | Get bundle details with file digest list. |
| `POST` | `/v1/admin/bundles/{ch}/{ver}/yank` | `{"yanked": true}` | Hide bundle from client index and check queries. |
| `DELETE` | `/v1/admin/bundles/{ch}/{ver}` | — | Delete bundle and storage (blocked if active). |

---

## End-to-End Walkthrough

Here is a typical lifecycle workflow using PowerShell and `curl.exe`:

### 1. Ingest a New Model Bundle (Staging)

```powershell
curl.exe -X POST "http://localhost:8000/v1/admin/bundles/import" `
  -H "Content-Type: application/json" `
  -d '{
    "source_path": "dist/bundle_v1_quant.zip",
    "channel": "beta",
    "quantized": true,
    "promote": false,
    "allow_unsigned": true,
    "changelog": ["First beta drop", "seg_lung retrained with fp16"]
  }'
```

### 2. Promote to Active

```powershell
curl.exe -X POST "http://localhost:8000/v1/admin/channels/beta/promote" `
  -H "Content-Type: application/json" `
  -d '{"version": "2026.09.04", "mandatory": false}'
```

### 3. Check for Updates from Edge Client

```powershell
curl.exe "http://localhost:8000/v1/models/check?channel=beta&current=2026.01.01&app=1.0.0"
```

### 4. Resumable Chunked Download (HTTP Range)

```powershell
# Download bytes 0 through 1023
curl.exe -i -r 0-1023 -o part.bin `
  "http://localhost:8000/v1/models/beta/2026.09.04/bundle.zip"
```

### 5. Conditional Caching (ETag & 304 Not Modified)

```powershell
# First fetch to obtain ETag header
curl.exe -i "http://localhost:8000/v1/models/beta/2026.09.04/manifest.json"

# Subsequent request using If-None-Match returns 304 Not Modified
curl.exe -i -H 'If-None-Match: "<etag-from-previous-response>"' `
  "http://localhost:8000/v1/models/beta/2026.09.04/manifest.json"
```

### 6. Emergency Rollback

```powershell
curl.exe -X POST "http://localhost:8000/v1/admin/channels/beta/rollback"
```

---

## Codebase Layout

```
demo_api/
├── app/
│   ├── main.py          # FastAPI application, lifecycle hooks, and error handlers
│   ├── config.py        # Settings configuration via pydantic-settings (DEMO_API_*)
│   ├── database.py      # SQLAlchemy 2.0 engine, sessions, lightweight migrations
│   ├── models.py        # Database ORM models: Channel, BundleVersion, BundleFile
│   ├── schemas.py       # Pydantic request/response schemas
│   ├── versioning.py    # Semantic version parsing and app compatibility checks
│   ├── storage.py       # On-disk directory resolution and path traversal defense
│   ├── bundle.py        # Safe extraction, digest verification, Ed25519 crypto check
│   ├── http_utils.py    # Canonical JSON hashing and ETag response generation
│   ├── services.py      # Catalog logic, promotion/rollback, bundle publication
│   └── routers/
│       ├── catalog.py   # Public endpoints: /v1/models/index.json, /v1/models/check
│       ├── artifacts.py # Static file delivery with Range & immutable caching
│       └── admin.py     # Channel and bundle administrative management endpoints
├── keys/
│   └── bundle_signing_key_pub.pem # Pinned Ed25519 public key
├── scripts/
│   └── seed.py          # Seeder script importing pipeline distribution bundles
├── storage/             # Stored bundle files (created at runtime, gitignored)
├── .env.example         # Example configuration settings
├── README.md            # Quickstart and project overview (this file)
└── DOCS.md              # In-depth architectural and operational documentation
```

---

## Detailed Documentation

For exhaustive information on the pipeline graph execution order, cryptographic verification internals, client update state machines, and API schemas, refer to:

👉 **[Complete Technical Specification & Architecture Guide (DOCS.md)](DOCS.md)**
