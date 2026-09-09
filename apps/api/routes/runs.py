"""Run management, projection, decision, and evidence routes.

Error handling is product-safe: known conditions map to truthful 4xx responses
with messages the service controls, and any unexpected failure becomes a generic
500 without leaking internal exception text, paths, or tracebacks.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

# Server-side error logging: the full exception (with traceback) is recorded to
# CloudWatch for operability, while the client still receives only the generic,
# product-safe message below. This never leaks internal detail to the caller.
_log = logging.getLogger("aircheck.api")

from apps.api.models import (
    CreateRunRequest,
    CreateRunResponse,
    DecideRequest,
    DecideResponse,
    DeliveriesResponse,
    EvidenceResponse,
    PendingDecisionView,
    RunDetailView,
)
from aircheck.persistence.s3_lock import ConflictError
from apps.api.service import AIRCheckService, StateUnavailableError


router = APIRouter(tags=["Runs"])

_INTERNAL_ERROR = "AIRCheck could not complete the request. No run data was changed."


def _conflict(exc: Exception) -> HTTPException:
    # Concurrent-mutation conflict message is authored by the service (product-safe).
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _unavailable(exc: Exception) -> HTTPException:
    # Fail-closed: durable state could not be confirmed. No run data was changed.
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
    )


def get_service() -> AIRCheckService:
    from apps.api.main import service

    return service


def _not_found(exc: Exception, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


def _bad_request(exc: Exception) -> HTTPException:
    # ValueError messages are authored by the service and are product-safe.
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _internal(exc: Exception) -> HTTPException:
    _log.error("AIRCheck request failed", exc_info=exc)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=_INTERNAL_ERROR)


@router.get("/runs", response_model=DeliveriesResponse)
def list_runs(svc: AIRCheckService = Depends(get_service)) -> DeliveriesResponse:
    """Return the cross-run operational ledger and counts."""
    try:
        return svc.list_runs()
    except Exception as exc:  # pragma: no cover - defensive
        raise _internal(exc) from exc


@router.post(
    "/runs", response_model=CreateRunResponse, status_code=status.HTTP_201_CREATED
)
def create_run(
    req: CreateRunRequest, svc: AIRCheckService = Depends(get_service)
) -> CreateRunResponse:
    """Create and execute a new delivery check run using existing runtime behavior."""
    try:
        return svc.create_run(req)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except Exception as exc:
        raise _internal(exc) from exc


@router.get("/runs/{run_id}", response_model=RunDetailView)
def get_run(run_id: str, svc: AIRCheckService = Depends(get_service)) -> RunDetailView:
    """Return the full control-room projection for a run."""
    try:
        return svc.get_run(run_id)
    except KeyError as exc:
        raise _not_found(exc, f"Run {run_id} was not found.") from exc
    except Exception as exc:
        raise _internal(exc) from exc


@router.get("/runs/{run_id}/decision", response_model=PendingDecisionView)
def get_decision(
    run_id: str, svc: AIRCheckService = Depends(get_service)
) -> PendingDecisionView:
    """Return the pending (or most recently resolved) human decision for a run."""
    try:
        return svc.get_decision(run_id)
    except KeyError as exc:
        raise _not_found(exc, f"Run {run_id} was not found.") from exc
    except ValueError as exc:
        raise _not_found(exc, str(exc)) from exc
    except Exception as exc:
        raise _internal(exc) from exc


@router.post("/runs/{run_id}/decision", response_model=DecideResponse)
def submit_decision(
    run_id: str,
    req: DecideRequest,
    svc: AIRCheckService = Depends(get_service),
) -> DecideResponse:
    """Submit a human decision for a run paused at an authority boundary."""
    try:
        return svc.submit_decision(run_id=run_id, approved=req.approved, actor=req.actor)
    except KeyError as exc:
        raise _not_found(exc, f"Run {run_id} was not found.") from exc
    except ConflictError as exc:
        # Another operator/container already resolved this decision (concurrency).
        raise _conflict(exc) from exc
    except StateUnavailableError as exc:
        raise _unavailable(exc) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except Exception as exc:
        raise _internal(exc) from exc


@router.get("/runs/{run_id}/evidence", response_model=EvidenceResponse)
def get_evidence(
    run_id: str, svc: AIRCheckService = Depends(get_service)
) -> EvidenceResponse:
    """Return terminal evidence overview and the authentic artifact list."""
    try:
        return svc.get_evidence(run_id)
    except KeyError as exc:
        raise _not_found(exc, f"Run {run_id} was not found.") from exc
    except Exception as exc:
        raise _internal(exc) from exc


@router.get("/runs/{run_id}/evidence/{filename}")
def download_evidence_file(
    run_id: str,
    filename: str,
    svc: AIRCheckService = Depends(get_service),
):
    """Download an authentic evidence artifact file."""
    try:
        file_path = svc.get_evidence_file_path(run_id, filename)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except FileNotFoundError as exc:
        raise _not_found(exc, f"Evidence artifact {filename} is not available.") from exc
    except KeyError as exc:
        raise _not_found(exc, f"Run {run_id} was not found.") from exc
    except Exception as exc:
        raise _internal(exc) from exc
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )
