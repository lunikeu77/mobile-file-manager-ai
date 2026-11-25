from __future__ import annotations

import io
import logging

import pytest

from backend.security.crypto import SecretRedactionFilter, redact_sensitive_text
from backend.security.validator import SpecValidationError, validate_ai_task_spec
from backend.services.ai_providers import AIResult, ProviderAuthError, ProviderTransientError


@pytest.mark.asyncio
async def test_gate2_ai_failover_429_and_stop_on_401(container):
    """
    [검증 2] AI 어댑터 Failover 테스트:
    1) OpenRouter 429 응답 시 보조 공급자(Gemini)로 전환되는가?
    2) 401/403 응답 시 무한 재시도 없이 즉시 설정 오류로 처리되는가?
    """
    await container.local_files.save_bytes_stream("clip.mp4", io.BytesIO(b"0000"))
    files = await container.local_files.list_files(source="local")
    fid = files[0]["file_id"]

    # Configure encrypted keys for OpenRouter and Gemini
    await container.ai_router.update_provider_setting("openrouter", {"api_key": "sk-or-v1-testkey123456", "enabled": True, "priority": 1})
    await container.ai_router.update_provider_setting("gemini", {"api_key": "AIzaSyTestGeminiKey1234567890", "enabled": True, "priority": 2})
    container.ai_router.retry_base_delay_sec = 0.01

    # Verify API keys are NOT stored in plaintext in DB
    row = await container.db.fetch_one("SELECT key_reference FROM ai_provider_settings WHERE provider_id = 'openrouter'")
    assert row["key_reference"] == "ai_key:openrouter"
    secret_row = await container.db.fetch_one("SELECT ciphertext, masked_preview FROM encrypted_secrets WHERE key_reference = 'ai_key:openrouter'")
    assert "sk-or-v1-testkey123456" not in secret_row["ciphertext"]
    assert "••••" in secret_row["masked_preview"]

    calls: list[str] = []

    class MockOpenRouter429:
        provider_id = "openrouter"

        async def generate_task_proposal(self, api_key, model_id, request):
            calls.append("openrouter")
            raise ProviderTransientError("Rate limit exceeded (429)", status_code=429)

    class MockGeminiOk:
        provider_id = "gemini"

        async def generate_task_proposal(self, api_key, model_id, request):
            calls.append("gemini")
            return AIResult(
                provider_id="gemini",
                model_id=model_id,
                raw_json={
                    "schema_version": "1.0",
                    "intent": "compress_video",
                    "steps": [
                        {
                            "action": "COMPRESS_VIDEO",
                            "source": "local",
                            "file_id": fid,
                            "options": {"preset": "balanced"},
                        }
                    ],
                    "requires_confirmation": False,
                },
                token_usage={"totalTokenCount": 42},
                latency_ms=15,
            )

    container.ai_router.adapters["openrouter"] = MockOpenRouter429()
    container.ai_router.adapters["gemini"] = MockGeminiOk()

    # Non-L0 natural language command triggers AI Router
    plan = await container.ai_router.plan_command("최근 작업용 영상 하나 골라서 균형 모드로 용량 줄여줘", source="local")
    assert plan["provider_used"] == "gemini"
    assert calls == ["openrouter", "openrouter", "gemini"]  # 1 retry on openrouter, then failover to gemini
    assert len(plan["failover_chain"]) == 1
    assert plan["failover_chain"][0]["provider_id"] == "openrouter"

    # Now test 401/403: must NOT failover to next provider!
    calls.clear()

    class MockOpenRouter401:
        provider_id = "openrouter"

        async def generate_task_proposal(self, api_key, model_id, request):
            calls.append("openrouter_401")
            raise ProviderAuthError("Unauthorized API key", status_code=401)

    await container.db.execute("UPDATE ai_provider_settings SET circuit_breaker_until = NULL WHERE provider_id = 'openrouter'")
    container.ai_router.adapters["openrouter"] = MockOpenRouter401()

    with pytest.raises(ProviderAuthError):
        await container.ai_router.plan_command("적당한 압축 계획 세워줘", source="local")
    assert calls == ["openrouter_401"]  # Immediately halted, Gemini was NOT called

    or_state = await container.db.fetch_one("SELECT quota_state, last_error_code FROM ai_provider_settings WHERE provider_id = 'openrouter'")
    assert or_state["quota_state"] == "auth_error"
    assert or_state["last_error_code"] == "401"


@pytest.mark.asyncio
async def test_gate3_ai_security_schema_and_confirmation_gate(container):
    """
    [검증 3] AI 보안 검증 테스트:
    1) 허용되지 않은 action 차단
    2) 경로 탈출(../) 차단
    3) additionalProperties 위반 차단
    4) 존재하지 않는 file_id 차단
    5) DELETE_FILE / OVERWRITE_FILE은 사용자 승인 없이 실행되지 않음
    6) 로그/에러 문자열에서 API 키 및 토큰 마스킹 검증
    """
    await container.local_files.save_bytes_stream("important.txt", io.BytesIO(b"keep-me"))
    files = await container.local_files.list_files(source="local")
    fid = files[0]["file_id"]
    known_ids = {fid}

    # 1. Disallowed action (shell execution)
    with pytest.raises(SpecValidationError):
        validate_ai_task_spec(
            {"schema_version": "1.0", "intent": "hack", "steps": [{"action": "RUN_SHELL", "source": "local"}]},
            known_ids,
        )

    # 2. Path traversal in filename
    with pytest.raises(SpecValidationError):
        validate_ai_task_spec(
            {
                "schema_version": "1.0",
                "intent": "traversal",
                "steps": [{"action": "RENAME_FILE", "file_id": fid, "options": {"new_name": "../../etc/passwd"}}],
            },
            known_ids,
        )

    # 3. additionalProperties violation
    with pytest.raises(SpecValidationError):
        validate_ai_task_spec(
            {
                "schema_version": "1.0",
                "intent": "extra",
                "unexpected_key": "bad",
                "steps": [{"action": "SEARCH_FILES", "source": "local"}],
            },
            known_ids,
        )

    # 4. Unknown file_id fabricated by AI
    with pytest.raises(SpecValidationError):
        validate_ai_task_spec(
            {
                "schema_version": "1.0",
                "intent": "fake_id",
                "steps": [{"action": "DELETE_FILE", "file_id": "non_existent_id_999"}],
            },
            known_ids,
        )

    # 5. DELETE_FILE / OVERWRITE_FILE must NOT run without explicit user confirmation
    task = await container.task_engine.create_task(
        title="AI 제안 삭제",
        task_type="ai_workflow",
        confirmed=False,
        spec={
            "schema_version": "1.0",
            "intent": "delete_file",
            "steps": [{"action": "DELETE_FILE", "source": "local", "file_id": fid}],
            "requires_confirmation": False,  # even if AI lied and said false!
        },
    )
    assert task["status"] == "NEEDS_USER_ACTION"
    assert task["requires_confirmation"] == 1
    # Attempting to execute directly still refuses while unconfirmed
    res = await container.task_engine.execute_task(task["task_id"])
    assert res["status"] == "NEEDS_USER_ACTION"
    assert await container.local_files.get_file(fid) is not None

    # 6. Secret redaction in logs
    raw = "Authorization: Bearer my-secret-token-xyz AIzaSyAbcdefghijklmnopqrstuvwx sk-or-v1-secretkey999999"
    redacted = redact_sensitive_text(raw)
    assert "my-secret-token-xyz" not in redacted
    assert "AIzaSyAbcdefghijklmnopqrstuvwx" not in redacted
    assert "sk-or-v1-secretkey999999" not in redacted

    filt = SecretRedactionFilter()
    rec = logging.LogRecord("test", logging.INFO, "", 0, "Using key %s", ("sk-secret-key-1234567890",), None)
    filt.filter(rec)
    assert "sk-secret-key-1234567890" not in rec.getMessage()
