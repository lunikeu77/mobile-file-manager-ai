from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.models import PrivacySettingsUpdate, ProviderSettingUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/providers")
async def get_providers(request: Request):
    container = request.app.state.container
    items = await container.ai_router.list_provider_settings()
    return {"ok": True, "data": items}


@router.put("/providers/{provider_id}")
async def update_provider(request: Request, provider_id: str, body: ProviderSettingUpdate):
    container = request.app.state.container
    try:
        updated = await container.ai_router.update_provider_setting(
            provider_id, body.model_dump(exclude_unset=True)
        )
        return {"ok": True, "data": updated}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/providers/{provider_id}/test")
async def test_provider(request: Request, provider_id: str):
    container = request.app.state.container
    try:
        res = await container.ai_router.test_provider_connection(provider_id)
        return {"ok": res.get("ok", False), "data": res}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/privacy")
async def get_privacy_settings(request: Request):
    container = request.app.state.container
    prefs = await container.db.get_preferences()
    return {
        "ok": True,
        "data": {
            "ai_send_filenames": prefs.get("ai_send_filenames", "false") == "true",
            "ai_send_metadata": prefs.get("ai_send_metadata", "true") == "true",
            "ai_free_only_mode": prefs.get("ai_free_only_mode", "true") == "true",
            "ai_auto_failover": prefs.get("ai_auto_failover", "true") == "true",
            "ai_monthly_limit_usd": float(prefs.get("ai_monthly_limit_usd", "0.0")),
            "temp_cleanup_hours": int(prefs.get("temp_cleanup_hours", "24")),
            "last_cleanup_at": prefs.get("last_cleanup_at"),
            "using_ephemeral_secret": container.crypto.using_ephemeral_secret,
            "security_notice": (
                "개인 API 키와 OAuth 토큰은 ENCRYPTION_SECRET 기반 Fernet 대칭 암호화 후 SQLite에 저장되며, "
                "로그·에러 메시지·URL 쿼리에 출력되지 않습니다. 단, 서버 메모리 내 런타임 복호화 및 호스팅 환경 관리자 접근으로부터의 "
                "완전한 하드웨어 격리(HSM)를 의미하지는 않으므로, 공유 공용 키 등록은 금지하며 개인 키만 등록하세요."
            ),
        },
    }


@router.put("/privacy")
async def update_privacy_settings(request: Request, body: PrivacySettingsUpdate):
    container = request.app.state.container
    updates = body.model_dump(exclude_none=True)
    for k, v in updates.items():
        if isinstance(v, bool):
            await container.db.set_preference(k, "true" if v else "false")
        else:
            await container.db.set_preference(k, str(v))
    return await get_privacy_settings(request)
