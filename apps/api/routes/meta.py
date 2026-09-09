"""Metadata and canonical state vocabulary routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from apps.api.models import MetaStatesResponse
from apps.api.service import AIRCheckService


router = APIRouter(tags=["Metadata"])


def get_service() -> AIRCheckService:
    from apps.api.main import service
    return service


@router.get("/meta/states", response_model=MetaStatesResponse)
def get_meta_states(svc: AIRCheckService = Depends(get_service)) -> MetaStatesResponse:
    """Return the authoritative state and status vocabulary and profiles."""
    return svc.get_meta_states()
