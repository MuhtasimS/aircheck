"""AIRCheck FastAPI application exposing read projections and command seams.

M7 deployment notes. The application boundary is unchanged in what it *decides*
(nothing — the deterministic runtime owns all truth); M7 only makes it deployable:
``/health`` reports the real deployed Git SHA and runtime modes, startup rehydrates
durable S3 state on a fresh container (production disk is a disposable cache), and
CORS origins are configurable. Errors remain product-safe (see ``routes/runs.py``).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apps.api.routes import meta, runs
from apps.api.service import AIRCheckService, StateUnavailableError

service = AIRCheckService()

_STATE_UNAVAILABLE = "AIRCheck durable state is temporarily unavailable. Please retry."

GIT_SHA = os.environ.get("AIRCHECK_GIT_SHA", "unknown")
GATE = os.environ.get("AIRCHECK_GATE", "M7")
REGION = os.environ.get("AWS_REGION", "us-east-1")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Prepare durable state for this container.

    In production (S3 configured) a fresh container rehydrates the durable run
    state from S3 and does NOT run the heavy synthetic seed in the init path
    (which is bounded to ~10s on Lambda); demonstration runs are created through
    the real ``POST /runs`` pipeline. Locally the provider-free canonical runs are
    seeded on startup exactly as in M5.
    """
    try:
        if service.s3_enabled:
            # Hydrate only the small authoritative run index at startup (M8); the
            # per-run media/evidence workspace is hydrated lazily on access.
            service.sync_index()
        else:
            service.seed_canonical_runs()
    except Exception:
        # Best-effort at startup; the per-request path re-attempts and fails closed
        # (503) if a cold container still cannot establish authoritative state.
        pass
    yield


# The interactive API docs (`/docs`, `/redoc`, `/openapi.json`) add no judging value
# for this bounded synthetic-universe demo and widen the public surface, so they are
# disabled by default (M8 audit P8). Set AIRCHECK_ENABLE_DOCS=1 to re-enable locally.
_docs_enabled = os.environ.get("AIRCHECK_ENABLE_DOCS", "").lower() in {"1", "true", "yes"}

app = FastAPI(
    title="AIRCheck API",
    description="Deterministic media QC agent read projections and command seams.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

_cors_origins = [
    origin.strip()
    for origin in os.environ.get("AIRCHECK_CORS_ORIGINS", "*").split(",")
    if origin.strip()
] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # AIRCheck uses no cookies or credentialed requests; keep this False so a
    # wildcard origin is spec-valid and no credentials are ever reflected.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def _rehydrate_durable_state(request, call_next):
    """Rehydrate the durable run INDEX from S3 before serving a request.

    The account concurrency limit prevents reserving the Lambda to a single
    container, so a request may land on any warm/cold container. This syncs only
    the small authoritative run index (snapshots/events/ledger, ~1 MB), not the
    ~100 MB media/evidence workspace — that is hydrated lazily per accessed run by
    the service. Incremental (content-hash) sync keeps it cheap and keeps the
    Deliveries/Control-Room projections fresh across containers. Health checks skip
    it. No-op when S3 is not configured (local/dev/test).

    Fail closed, not silent (M8 audit P2): a container that has never established
    authoritative state returns a product-safe 503 rather than serve empty/stale
    state as current.
    """
    if service.s3_enabled and not request.url.path.endswith("/health"):
        try:
            service.sync_index()
        except StateUnavailableError:
            return JSONResponse(status_code=503, content={"detail": _STATE_UNAVAILABLE})
    return await call_next(request)


# Root routes
app.include_router(meta.router)
app.include_router(runs.router)

# Also mount under /api prefix for API clients that prefix requests
app.include_router(meta.router, prefix="/api")
app.include_router(runs.router, prefix="/api")


def _health() -> dict:
    """Truthful deployment health: the real deployed revision and runtime modes.

    No secrets, ARNs, or account identifiers are exposed — only the deployed Git
    SHA and product-safe booleans/labels.
    """
    return {
        "status": "ok",
        "gate": GATE,
        "git_sha": GIT_SHA,
        "region": REGION,
        "persistence": "s3" if service.s3_enabled else "local",
        "agentcore": "enabled" if service.agentcore_enabled else "fallback",
    }


@app.get("/health")
def health():
    return _health()


@app.get("/api/health")
def api_health():
    return _health()
