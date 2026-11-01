"""Run routes."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import RunServiceDep
from app.schemas.runs import RunResponse, StartRunRequest

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
def start_run(request: StartRunRequest, service: RunServiceDep) -> RunResponse:
    """Queue a run of a stored tender; poll `GET /runs/{run_id}` for its status."""
    return service.start(request)


@router.get("", response_model=list[RunResponse], status_code=status.HTTP_200_OK)
def list_runs(service: RunServiceDep) -> list[RunResponse]:
    """Recent runs, newest first."""
    return service.list_recent()


@router.get("/{run_id}", response_model=RunResponse, status_code=status.HTTP_200_OK)
def get_run(run_id: uuid.UUID, service: RunServiceDep) -> RunResponse:
    """A run's status, and the reason if it failed."""
    return service.get(run_id)
