from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from backend.models import CreateTaskRequest
from backend.security.validator import SpecValidationError

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(request: Request, status: str | None = Query(None)):
    container = request.app.state.container
    items = await container.task_engine.list_tasks(status=status)
    return {"ok": True, "data": items}


@router.post("")
async def create_task(request: Request, body: CreateTaskRequest):
    container = request.app.state.container
    try:
        task = await container.task_engine.create_task(
            title=body.title,
            task_type=body.task_type,
            spec=body.spec,
            scheduled_at=body.scheduled_at,
            idempotency_key=body.idempotency_key,
            confirmed=body.confirmed,
        )
        return {"ok": True, "data": task}
    except SpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}")
async def get_task(request: Request, task_id: str):
    container = request.app.state.container
    try:
        task = await container.task_engine.get_task_detail(task_id)
        return {"ok": True, "data": task}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/confirm")
async def confirm_task(request: Request, task_id: str):
    container = request.app.state.container
    try:
        task = await container.task_engine.confirm_task(task_id)
        return {"ok": True, "data": task}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/retry")
async def retry_task(request: Request, task_id: str):
    container = request.app.state.container
    try:
        task = await container.task_engine.retry_task(task_id)
        return {"ok": True, "data": task}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/cancel")
async def cancel_task(request: Request, task_id: str):
    container = request.app.state.container
    try:
        task = await container.task_engine.cancel_task(task_id)
        return {"ok": True, "data": task}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
