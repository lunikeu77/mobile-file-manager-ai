from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.models import AIChatRequest, AICommandRequest
from backend.security.validator import SpecValidationError
from backend.services.ai_providers import ProviderAuthError

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/command")
async def plan_ai_command(request: Request, body: AICommandRequest):
    container = request.app.state.container
    try:
        plan = await container.ai_router.plan_command(
            body.command,
            selected_file_ids=body.selected_file_ids,
            source=body.source,
            preferred_provider=body.preferred_provider,
            preferred_model=body.preferred_model,
        )
        return {"ok": True, "data": plan}
    except ProviderAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except SpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/chat")
async def chat_with_ai(request: Request, body: AIChatRequest):
    container = request.app.state.container
    try:
        res = await container.ai_router.chat_with_provider(
            provider_id=body.provider_id,
            model_id=body.model_id,
            message=body.message,
            history=[m.model_dump() for m in body.history],
            include_file_context=body.include_file_context,
        )
        return {"ok": True, "data": res}
    except ProviderAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except SpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/usage")
async def list_ai_usage(request: Request):
    container = request.app.state.container
    events = await container.db.fetch_all(
        "SELECT * FROM ai_usage_events ORDER BY created_at DESC LIMIT 100"
    )
    providers = await container.ai_router.list_provider_settings()
    openrouter_usage = None
    or_key = await container.ai_router.get_api_key("openrouter")
    if or_key:
        try:
            openrouter_usage = await container.ai_router.adapters["openrouter"].get_usage(or_key)
        except Exception:
            openrouter_usage = None
    return {
        "ok": True,
        "data": {
            "events": events,
            "providers": providers,
            "openrouter_live_usage": openrouter_usage,
        },
    }
