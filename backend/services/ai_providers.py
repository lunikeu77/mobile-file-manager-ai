from __future__ import annotations

import asyncio
import json
import random
import re
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from backend.config import Settings
from backend.db import Database, utc_now_iso
from backend.models import AllowedAction
from backend.security.crypto import redact_sensitive_text
from backend.security.validator import SpecValidationError, validate_ai_task_spec
from backend.services.local_files import LocalFileService
from backend.services.local_parser import parse_local_deterministic_command
from backend.services.secrets_store import EncryptedSecretStore

SYSTEM_PROMPT = """You are a planning assistant for a file management application.
You NEVER execute commands or access files directly. You ONLY output a single valid JSON object matching this exact schema:
{
  "schema_version": "1.0",
  "intent": "<short_intent_string>",
  "summary": "<korean_or_english_summary>",
  "steps": [
    {
      "action": "SEARCH_FILES | RENAME_FILE | CHANGE_EXTENSION | DOWNLOAD_FILE | COMPRESS_VIDEO | COMPRESS_PDF | UPLOAD_FILE | DELETE_FILE | OVERWRITE_FILE",
      "source": "local | google_drive | external_url",
      "file_id": "<existing_file_id_or_omit>",
      "input_ref": "<$previous.output_or_omit>",
      "filters": {"query": "...", "mime_type": "...", "extension": "...", "min_size_bytes": 0, "max_size_bytes": 0},
      "options": {"new_name": "...", "new_extension": "...", "suffix": "...", "preset": "...", "target_size_bytes": 0, "url": "..."}
    }
  ],
  "requires_confirmation": true,
  "warnings": []
}
Rules:
- Only use the allowed action enum values above.
- Do not include extra unknown keys.
- Only reference file_id values provided in the context, or use $previous.output for chaining.
- Output raw JSON only, with no markdown fences or explanation."""


class ProviderAuthError(RuntimeError):
    """Non-retryable 401/403 authentication or invalid key error."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderTransientError(RuntimeError):
    """Retryable/failover-eligible 429, 5xx, timeout, or network error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class AIRequest:
    command: str
    context_files: list[dict[str, Any]]
    selected_file_ids: list[str]
    source: str
    correlation_id: str


@dataclass
class AIResult:
    provider_id: str
    model_id: str
    raw_json: dict[str, Any]
    token_usage: dict[str, Any] | None
    latency_ms: int


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SpecValidationError(f"AI response was not valid JSON: {exc}") from exc


def _is_paid_model(provider_id: str, model_id: str) -> bool:
    if provider_id in {"openai", "kimi"}:
        return True
    if provider_id == "openrouter":
        return not (model_id == "openrouter/free" or model_id.endswith(":free"))
    return False


class AIProviderAdapter(ABC):
    provider_id: str

    @abstractmethod
    async def check_health(self, api_key: str, model_id: str) -> dict[str, Any]:
        ...

    @abstractmethod
    async def generate_task_proposal(self, api_key: str, model_id: str, request: AIRequest) -> AIResult:
        ...

    @abstractmethod
    async def chat_completion(
        self,
        api_key: str,
        model_id: str,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    async def get_usage(self, api_key: str) -> dict[str, Any] | None:
        ...


async def _openai_compat_chat(
    *,
    provider_id: str,
    url: str,
    api_key: str,
    model_id: str,
    messages: list[dict[str, str]],
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **(extra_headers or {})}
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            resp = await client.post(
                url,
                headers=headers,
                json={"model": model_id, "messages": messages, "temperature": 0.3},
            )
    except httpx.TimeoutException as exc:
        raise ProviderTransientError(f"{provider_id} request timed out: {exc}") from exc
    except httpx.HTTPError as exc:
        raise ProviderTransientError(f"{provider_id} network error: {exc}") from exc

    latency = int((time.monotonic() - t0) * 1000)
    if resp.status_code in {401, 403}:
        raise ProviderAuthError(f"{provider_id} authentication failed ({resp.status_code}).", resp.status_code)
    if resp.status_code == 429 or resp.status_code >= 500:
        detail = redact_sensitive_text(resp.text[:250])
        raise ProviderTransientError(f"{provider_id} error ({resp.status_code}): {detail}", resp.status_code)
    if resp.status_code >= 400:
        raise SpecValidationError(f"{provider_id} rejected request ({resp.status_code}): {redact_sensitive_text(resp.text[:250])}")

    data = resp.json()
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise SpecValidationError(f"{provider_id} returned empty choices.")
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise SpecValidationError(f"{provider_id} returned empty message content.")
    return {"reply": content, "usage": data.get("usage"), "latency_ms": latency}


def _build_user_prompt(request: AIRequest) -> str:
    payload = {
        "user_command": request.command,
        "default_source": request.source,
        "selected_file_ids": request.selected_file_ids,
        "available_files_metadata": request.context_files,
    }
    return json.dumps(payload, ensure_ascii=False)


class OpenRouterAdapter(AIProviderAdapter):
    provider_id = "openrouter"
    BASE_URL = "https://openrouter.ai/api/v1"

    async def check_health(self, api_key: str, model_id: str) -> dict[str, Any]:
        usage = await self.get_usage(api_key)
        return {"ok": True, "provider_id": self.provider_id, "usage": usage}

    async def get_usage(self, api_key: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code in {401, 403}:
            raise ProviderAuthError("OpenRouter API key rejected (401/403).", resp.status_code)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderTransientError(f"OpenRouter key check transient error ({resp.status_code}).", resp.status_code)
        resp.raise_for_status()
        body = resp.json()
        # Safely parse without hardcoding undocumented schema assumptions
        data = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else body
        return {
            "label": data.get("label") if isinstance(data, dict) else None,
            "limit": data.get("limit") if isinstance(data, dict) else None,
            "limit_remaining": data.get("limit_remaining") if isinstance(data, dict) else None,
            "usage": data.get("usage") if isinstance(data, dict) else None,
            "is_free_tier": data.get("is_free_tier") if isinstance(data, dict) else None,
            "rate_limit": data.get("rate_limit") if isinstance(data, dict) else None,
        }

    async def generate_task_proposal(self, api_key: str, model_id: str, request: AIRequest) -> AIResult:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "X-Title": "Mobile File Manager AI",
                    },
                    json={
                        "model": model_id,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": _build_user_prompt(request)},
                        ],
                        "temperature": 0.1,
                    },
                )
        except httpx.TimeoutException as exc:
            raise ProviderTransientError(f"OpenRouter request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(f"OpenRouter network error: {exc}") from exc

        latency = int((time.monotonic() - t0) * 1000)
        if resp.status_code in {401, 403}:
            raise ProviderAuthError(f"OpenRouter authentication failed ({resp.status_code}).", resp.status_code)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderTransientError(f"OpenRouter rate limit or server error ({resp.status_code}).", resp.status_code)
        if resp.status_code >= 400:
            raise SpecValidationError(f"OpenRouter rejected request ({resp.status_code}): {redact_sensitive_text(resp.text[:250])}")

        data = resp.json()
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise SpecValidationError("OpenRouter returned empty choices (filtered or malformed response).")
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise SpecValidationError("OpenRouter returned empty message content.")
        return AIResult(
            provider_id=self.provider_id,
            model_id=model_id,
            raw_json=_extract_json_object(content),
            token_usage=data.get("usage"),
            latency_ms=latency,
        )

    async def chat_completion(self, api_key: str, model_id: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        return await _openai_compat_chat(
            provider_id=self.provider_id,
            url=f"{self.BASE_URL}/chat/completions",
            api_key=api_key,
            model_id=model_id,
            messages=messages,
            extra_headers={"X-Title": "Mobile File Manager AI"},
        )


class GeminiAdapter(AIProviderAdapter):
    provider_id = "gemini"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    async def check_health(self, api_key: str, model_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/models/{model_id}",
                headers={"x-goog-api-key": api_key},
            )
        if resp.status_code in {401, 403}:
            raise ProviderAuthError("Gemini API key rejected (401/403).", resp.status_code)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderTransientError(f"Gemini health check error ({resp.status_code}).", resp.status_code)
        resp.raise_for_status()
        return {"ok": True, "provider_id": self.provider_id, "model": model_id}

    async def get_usage(self, api_key: str) -> dict[str, Any] | None:
        # Gemini Developer API limits depend on project/tier & are enforced dynamically via 429 responses.
        return {
            "note": "Gemini Developer API 한도는 프로젝트·모델별 동적 RPM/TPM/RPD 정책을 따르며 429 응답 기준으로 추적됩니다."
        }

    async def generate_task_proposal(self, api_key: str, model_id: str, request: AIRequest) -> AIResult:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/models/{model_id}:generateContent",
                    headers={
                        "x-goog-api-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                        "contents": [{"role": "user", "parts": [{"text": _build_user_prompt(request)}]}],
                        "generationConfig": {
                            "temperature": 0.1,
                            "responseMimeType": "application/json",
                        },
                    },
                )
        except httpx.TimeoutException as exc:
            raise ProviderTransientError(f"Gemini request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(f"Gemini network error: {exc}") from exc

        latency = int((time.monotonic() - t0) * 1000)
        if resp.status_code in {401, 403}:
            raise ProviderAuthError(f"Gemini authentication failed ({resp.status_code}).", resp.status_code)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderTransientError(f"Gemini quota/rate-limit or server error ({resp.status_code}).", resp.status_code)
        if resp.status_code >= 400:
            raise SpecValidationError(f"Gemini rejected request ({resp.status_code}): {redact_sensitive_text(resp.text[:250])}")

        data = resp.json()
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise SpecValidationError("Gemini returned empty candidates (possibly blocked by safety filters).")
        content_obj = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        parts = content_obj.get("parts") if isinstance(content_obj, dict) else None
        if not isinstance(parts, list) or not parts:
            raise SpecValidationError("Gemini returned empty content parts.")
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text.strip():
            raise SpecValidationError("Gemini returned empty text response.")
        return AIResult(
            provider_id=self.provider_id,
            model_id=model_id,
            raw_json=_extract_json_object(text),
            token_usage=data.get("usageMetadata"),
            latency_ms=latency,
        )

    async def chat_completion(self, api_key: str, model_id: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        t0 = time.monotonic()
        contents = [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
            if m["role"] != "system"
        ]
        sys_texts = [m["content"] for m in messages if m["role"] == "system"]
        body: dict[str, Any] = {"contents": contents, "generationConfig": {"temperature": 0.3}}
        if sys_texts:
            body["system_instruction"] = {"parts": [{"text": "\n".join(sys_texts)}]}
        try:
            async with httpx.AsyncClient(timeout=40.0) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/models/{model_id}:generateContent",
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTransientError(f"Gemini request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(f"Gemini network error: {exc}") from exc
        latency = int((time.monotonic() - t0) * 1000)
        if resp.status_code in {401, 403}:
            raise ProviderAuthError(f"Gemini authentication failed ({resp.status_code}).", resp.status_code)
        if resp.status_code == 429 or resp.status_code >= 500:
            detail = redact_sensitive_text(resp.text[:250])
            raise ProviderTransientError(f"Gemini error ({resp.status_code}): {detail}", resp.status_code)
        if resp.status_code >= 400:
            raise SpecValidationError(f"Gemini rejected request ({resp.status_code}): {redact_sensitive_text(resp.text[:250])}")
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise SpecValidationError("Gemini returned empty candidates.")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        reply = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not reply.strip():
            raise SpecValidationError("Gemini returned empty text response.")
        return {"reply": reply, "usage": data.get("usageMetadata"), "latency_ms": latency}


class KimiAdapter(AIProviderAdapter):
    provider_id = "kimi"

    def __init__(self, base_url: str = "https://api.moonshot.ai/v1") -> None:
        self.base_url = base_url.rstrip("/")

    async def check_health(self, api_key: str, model_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code in {401, 403}:
            raise ProviderAuthError("Kimi (Moonshot) API key rejected (401/403).", resp.status_code)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderTransientError(f"Kimi health check error ({resp.status_code}).", resp.status_code)
        resp.raise_for_status()
        return {"ok": True, "provider_id": self.provider_id, "model": model_id}

    async def get_usage(self, api_key: str) -> dict[str, Any] | None:
        return {"note": "Moonshot AI (Kimi) API 키 잔액/사용량 기준으로 동작합니다."}

    async def generate_task_proposal(self, api_key: str, model_id: str, request: AIRequest) -> AIResult:
        res = await _openai_compat_chat(
            provider_id=self.provider_id,
            url=f"{self.base_url}/chat/completions",
            api_key=api_key,
            model_id=model_id,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(request)},
            ],
        )
        return AIResult(
            provider_id=self.provider_id,
            model_id=model_id,
            raw_json=_extract_json_object(res["reply"]),
            token_usage=res.get("usage"),
            latency_ms=res["latency_ms"],
        )

    async def chat_completion(self, api_key: str, model_id: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        return await _openai_compat_chat(
            provider_id=self.provider_id,
            url=f"{self.base_url}/chat/completions",
            api_key=api_key,
            model_id=model_id,
            messages=messages,
        )


class OpenAIAdapter(AIProviderAdapter):
    provider_id = "openai"
    BASE_URL = "https://api.openai.com/v1"

    async def check_health(self, api_key: str, model_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code in {401, 403}:
            raise ProviderAuthError("OpenAI API key rejected (401/403).", resp.status_code)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderTransientError(f"OpenAI health check error ({resp.status_code}).", resp.status_code)
        resp.raise_for_status()
        return {
            "ok": True,
            "provider_id": self.provider_id,
            "note": "ChatGPT 구독 여부와 무관하게 OpenAI API 키 및 API 조직 크레딧 기준으로 검증되었습니다.",
        }

    async def get_usage(self, api_key: str) -> dict[str, Any] | None:
        return {
            "note": "OpenAI API 과금은 ChatGPT Free/Plus 구독과 별개입니다. API 조직 크레딧 기준으로 사용됩니다."
        }

    async def generate_task_proposal(self, api_key: str, model_id: str, request: AIRequest) -> AIResult:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_id,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": _build_user_prompt(request)},
                        ],
                        "temperature": 0.1,
                        "response_format": {"type": "json_object"},
                    },
                )
        except httpx.TimeoutException as exc:
            raise ProviderTransientError(f"OpenAI request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(f"OpenAI network error: {exc}") from exc

        latency = int((time.monotonic() - t0) * 1000)
        if resp.status_code in {401, 403}:
            raise ProviderAuthError(f"OpenAI authentication failed ({resp.status_code}).", resp.status_code)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderTransientError(f"OpenAI rate limit or server error ({resp.status_code}).", resp.status_code)
        if resp.status_code >= 400:
            raise SpecValidationError(f"OpenAI rejected request ({resp.status_code}): {redact_sensitive_text(resp.text[:250])}")

        data = resp.json()
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise SpecValidationError("OpenRouter returned empty choices (filtered or malformed response).")
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise SpecValidationError("OpenRouter returned empty message content.")
        return AIResult(
            provider_id=self.provider_id,
            model_id=model_id,
            raw_json=_extract_json_object(content),
            token_usage=data.get("usage"),
            latency_ms=latency,
        )

    async def chat_completion(self, api_key: str, model_id: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        return await _openai_compat_chat(
            provider_id=self.provider_id,
            url=f"{self.BASE_URL}/chat/completions",
            api_key=api_key,
            model_id=model_id,
            messages=messages,
            extra_headers={"X-Title": "Mobile File Manager AI"},
        )


class AIRouter:
    """
    Multi-provider AI orchestrator:
    1) Always runs L0 deterministic local parser first (0 cost, works offline).
    2) Respects privacy settings (filename masking / metadata consent), free-only mode, and monthly spend cap.
    3) Failover with exponential backoff + jitter (max 2 attempts per provider) on 429/5xx/timeout.
    4) Stops immediately (no infinite failover) on 401/403 auth errors or invalid schema responses.
    5) Validates every AI output via strict `validate_ai_task_spec`.
    """

    def __init__(
        self,
        db: Database,
        settings: Settings,
        secrets_store: EncryptedSecretStore,
        local_files: LocalFileService,
    ) -> None:
        self.db = db
        self.settings = settings
        self.secrets = secrets_store
        self.local_files = local_files
        self.adapters: dict[str, AIProviderAdapter] = {
            "openrouter": OpenRouterAdapter(),
            "gemini": GeminiAdapter(),
            "kimi": KimiAdapter(settings.kimi_base_url),
            "openai": OpenAIAdapter(),
        }
        self.retry_base_delay_sec = 0.4

    async def get_api_key(self, provider_id: str) -> str | None:
        ref = f"ai_key:{provider_id}"
        stored = await self.secrets.get_secret(ref)
        if stored:
            return stored
        env_map = {
            "openrouter": self.settings.openrouter_api_key,
            "gemini": self.settings.gemini_api_key,
            "kimi": self.settings.kimi_api_key,
            "openai": self.settings.openai_api_key,
        }
        env_val = env_map.get(provider_id, "")
        return env_val or None

    async def list_provider_settings(self) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all("SELECT * FROM ai_provider_settings ORDER BY priority ASC")
        result: list[dict[str, Any]] = []
        for row in rows:
            pid = row["provider_id"]
            key = await self.get_api_key(pid)
            masked = await self.secrets.get_masked(f"ai_key:{pid}")
            if not masked and key:
                masked = "env_configured"
            result.append(
                {
                    "provider_id": pid,
                    "enabled": bool(row["enabled"]),
                    "priority": row["priority"],
                    "model_id": row["model_id"],
                    "key_configured": bool(key),
                    "key_masked": masked or "",
                    "key_reference": row["key_reference"],
                    "quota_state": row["quota_state"],
                    "last_error_at": row["last_error_at"],
                    "last_error_code": row["last_error_code"],
                    "last_error_message": row["last_error_message"],
                    "circuit_breaker_until": row["circuit_breaker_until"],
                    "free_only": bool(row["free_only"]),
                    "monthly_budget_usd": row["monthly_budget_usd"],
                    "updated_at": row["updated_at"],
                }
            )
        return result

    async def update_provider_setting(self, provider_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        if provider_id not in self.adapters:
            raise KeyError(f"Unknown AI provider: {provider_id}")
        now = utc_now_iso()
        if "api_key" in updates and updates["api_key"] is not None:
            raw_key = updates["api_key"].strip()
            ref = f"ai_key:{provider_id}"
            if raw_key == "":
                await self.secrets.delete_secret(ref)
                await self.db.execute(
                    "UPDATE ai_provider_settings SET key_reference = NULL, quota_state = 'unconfigured', last_error_code = NULL, last_error_message = NULL, updated_at = ? WHERE provider_id = ?",
                    (now, provider_id),
                )
            else:
                await self.secrets.set_secret(ref, raw_key)
                await self.db.execute(
                    "UPDATE ai_provider_settings SET key_reference = ?, quota_state = 'ready', last_error_code = NULL, last_error_message = NULL, circuit_breaker_until = NULL, updated_at = ? WHERE provider_id = ?",
                    (ref, now, provider_id),
                )

        fields = []
        params: list[Any] = []
        for col in ("enabled", "priority", "model_id", "free_only", "monthly_budget_usd"):
            if col in updates and updates[col] is not None:
                val = int(updates[col]) if col in {"enabled", "free_only"} else updates[col]
                fields.append(f"{col} = ?")
                params.append(val)
        if fields:
            fields.append("updated_at = ?")
            params.append(now)
            params.append(provider_id)
            await self.db.execute(f"UPDATE ai_provider_settings SET {', '.join(fields)} WHERE provider_id = ?", tuple(params))

        all_items = await self.list_provider_settings()
        return next(p for p in all_items if p["provider_id"] == provider_id)

    async def test_provider_connection(self, provider_id: str) -> dict[str, Any]:
        adapter = self.adapters.get(provider_id)
        if not adapter:
            raise KeyError(f"Unknown AI provider: {provider_id}")
        row = await self.db.fetch_one("SELECT * FROM ai_provider_settings WHERE provider_id = ?", (provider_id,))
        key = await self.get_api_key(provider_id)
        if not key:
            return {"ok": False, "provider_id": provider_id, "error": "API 키가 설정되지 않았습니다."}
        try:
            health = await adapter.check_health(key, row["model_id"])
            await self.db.execute(
                "UPDATE ai_provider_settings SET quota_state = 'ready', last_error_code = NULL, last_error_message = NULL, circuit_breaker_until = NULL, updated_at = ? WHERE provider_id = ?",
                (utc_now_iso(), provider_id),
            )
            return health
        except ProviderAuthError as exc:
            await self._record_provider_error(provider_id, "auth_error", str(exc.status_code), str(exc), open_circuit=False)
            return {"ok": False, "provider_id": provider_id, "quota_state": "auth_error", "error": str(exc)}
        except ProviderTransientError as exc:
            state = "rate_limited" if exc.status_code == 429 else "degraded"
            await self._record_provider_error(provider_id, state, str(exc.status_code or "net"), str(exc), open_circuit=True)
            return {"ok": False, "provider_id": provider_id, "quota_state": state, "error": str(exc)}

    async def _record_provider_error(
        self,
        provider_id: str,
        quota_state: str,
        error_code: str,
        message: str,
        *,
        open_circuit: bool,
    ) -> None:
        now = utc_now_iso()
        cb_until = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat() if open_circuit else None
        await self.db.execute(
            """
            UPDATE ai_provider_settings
            SET quota_state = ?, last_error_at = ?, last_error_code = ?, last_error_message = ?, circuit_breaker_until = ?, updated_at = ?
            WHERE provider_id = ?
            """,
            (quota_state, now, error_code, redact_sensitive_text(message)[:400], cb_until, now, provider_id),
        )

    async def _record_usage_event(
        self,
        *,
        provider_id: str,
        model_id: str,
        request_type: str,
        token_usage: dict[str, Any] | None,
        status_code: int,
        latency_ms: int,
        correlation_id: str,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO ai_usage_events
            (event_id, provider_id, model_id, request_type, token_usage_if_available, estimated_cost_usd, status_code, latency_ms, correlation_id, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                f"ai_{uuid.uuid4().hex[:12]}",
                provider_id,
                model_id,
                request_type,
                json.dumps(token_usage) if token_usage else None,
                status_code,
                latency_ms,
                correlation_id,
                utc_now_iso(),
            ),
        )

    async def _build_sanitized_context(self, source: str, prefs: dict[str, str]) -> tuple[list[dict[str, Any]], set[str]]:
        all_files = await self.local_files.list_files(source=source)
        known_ids = await self.local_files.get_all_known_file_ids()
        send_filenames = prefs.get("ai_send_filenames", "false") == "true"
        send_metadata = prefs.get("ai_send_metadata", "true") == "true"

        context: list[dict[str, Any]] = []
        if not send_metadata:
            return context, known_ids

        for idx, f in enumerate(all_files[:40], start=1):
            context.append(
                {
                    "file_id": f["file_id"],
                    "source": f["source"],
                    "name": f["name"] if send_filenames else f"[redacted_file_{idx}]",
                    "mime_type": f["mime_type"],
                    "size_bytes": f["size"],
                }
            )
        return context, known_ids

    async def plan_command(
        self,
        command: str,
        *,
        selected_file_ids: list[str] | None = None,
        source: str = "local",
        preferred_provider: str = "auto",
        preferred_model: str | None = None,
    ) -> dict[str, Any]:
        correlation_id = f"corr_{uuid.uuid4().hex[:12]}"
        known_ids = await self.local_files.get_all_known_file_ids()

        # Step 1: L0 Local Rule Engine (always first, zero external calls)
        l0 = parse_local_deterministic_command(command, selected_file_ids=selected_file_ids, source=source)
        if l0 is not None:
            validated = validate_ai_task_spec(l0["spec"], known_ids, allow_localhost_urls=self.settings.app_env != "production")
            return {
                "correlation_id": correlation_id,
                "complexity": l0["complexity"],
                "provider_used": l0["provider_used"],
                "model_id": "deterministic-rules-v1",
                "failover_chain": [],
                "spec": validated.model_dump(mode="json"),
            }

        # Step 2: External AI Provider Router
        prefs = await self.db.get_preferences()
        free_only_global = prefs.get("ai_free_only_mode", "false") == "true"
        auto_failover = prefs.get("ai_auto_failover", "true") == "true"
        monthly_limit_usd = float(prefs.get("ai_monthly_limit_usd", "0.0") or 0.0)
        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        spent_row = await self.db.fetch_one(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0.0) AS total_spent FROM ai_usage_events WHERE created_at >= ?",
            (month_start,),
        )
        monthly_spent_usd = float(spent_row["total_spent"]) if spent_row else 0.0

        context_files, known_ids = await self._build_sanitized_context(source, prefs)
        ai_req = AIRequest(
            command=command,
            context_files=context_files,
            selected_file_ids=selected_file_ids or [],
            source=source,
            correlation_id=correlation_id,
        )

        providers = await self.db.fetch_all("SELECT * FROM ai_provider_settings WHERE enabled = 1 ORDER BY priority ASC")
        if preferred_provider and preferred_provider != "auto":
            selected_row = await self.db.fetch_one(
                "SELECT * FROM ai_provider_settings WHERE provider_id = ?",
                (preferred_provider,),
            )
            if selected_row:
                providers = [selected_row] + [p for p in providers if p["provider_id"] != preferred_provider]

        now_dt = datetime.now(timezone.utc)
        failover_chain: list[dict[str, Any]] = []

        for prov in providers:
            pid = prov["provider_id"]
            model_id = (
                preferred_model.strip()
                if (preferred_model and preferred_provider == pid)
                else prov["model_id"]
            )
            is_paid_provider = _is_paid_model(pid, str(model_id))

            if free_only_global and is_paid_provider:
                failover_chain.append({"provider_id": pid, "skipped": "free_only_mode_enabled"})
                continue

            if monthly_limit_usd > 0 and is_paid_provider and monthly_spent_usd >= monthly_limit_usd:
                failover_chain.append({"provider_id": pid, "skipped": "monthly_budget_exceeded"})
                continue

            key = await self.get_api_key(pid)
            if not key:
                failover_chain.append({"provider_id": pid, "skipped": "no_api_key"})
                continue

            if prov.get("circuit_breaker_until"):
                cb_dt = datetime.fromisoformat(prov["circuit_breaker_until"])
                if cb_dt > now_dt:
                    failover_chain.append({"provider_id": pid, "skipped": "circuit_breaker_open"})
                    continue

            adapter = self.adapters[pid]

            # Max 2 attempts with exponential backoff + jitter on transient errors
            for attempt in range(2):
                try:
                    result = await adapter.generate_task_proposal(key, model_id, ai_req)
                    validated = validate_ai_task_spec(
                        result.raw_json,
                        known_ids,
                        allow_localhost_urls=self.settings.app_env != "production",
                    )
                    await self.db.execute(
                        "UPDATE ai_provider_settings SET quota_state = 'ready', circuit_breaker_until = NULL, updated_at = ? WHERE provider_id = ?",
                        (utc_now_iso(), pid),
                    )
                    await self._record_usage_event(
                        provider_id=pid,
                        model_id=model_id,
                        request_type="plan_command",
                        token_usage=result.token_usage,
                        status_code=200,
                        latency_ms=result.latency_ms,
                        correlation_id=correlation_id,
                    )
                    return {
                        "correlation_id": correlation_id,
                        "complexity": "L2/L3",
                        "provider_used": pid,
                        "model_id": model_id,
                        "latency_ms": result.latency_ms,
                        "failover_chain": failover_chain,
                        "spec": validated.model_dump(mode="json"),
                    }
                except ProviderAuthError as exc:
                    # 401/403 must NOT endlessly fail over; report configuration error immediately!
                    await self._record_provider_error(pid, "auth_error", str(exc.status_code), str(exc), open_circuit=False)
                    await self._record_usage_event(
                        provider_id=pid,
                        model_id=model_id,
                        request_type="plan_command",
                        token_usage=None,
                        status_code=exc.status_code,
                        latency_ms=0,
                        correlation_id=correlation_id,
                    )
                    raise ProviderAuthError(
                        f"{pid} 인증 오류 ({exc.status_code}): API 키 설정을 확인하세요. 무한 재시도를 중단했습니다.",
                        exc.status_code,
                    ) from exc
                except SpecValidationError as exc:
                    # Malformed or unsafe AI output: do not retry endlessly or run unvalidated spec
                    await self._record_provider_error(pid, "schema_error", "schema", str(exc), open_circuit=False)
                    raise SpecValidationError(f"AI 응답이 보안/스키마 검증을 통과하지 못했습니다: {exc}") from exc
                except ProviderTransientError as exc:
                    state = "rate_limited" if exc.status_code == 429 else "degraded"
                    await self._record_usage_event(
                        provider_id=pid,
                        model_id=model_id,
                        request_type="plan_command",
                        token_usage=None,
                        status_code=exc.status_code or 503,
                        latency_ms=0,
                        correlation_id=correlation_id,
                    )
                    if attempt == 0:
                        jitter = random.uniform(0.05, 0.25)
                        await asyncio.sleep(self.retry_base_delay_sec + jitter)
                        continue
                    await self._record_provider_error(pid, state, str(exc.status_code or "503"), str(exc), open_circuit=True)
                    failover_chain.append({"provider_id": pid, "error": str(exc), "status_code": exc.status_code})
                    break

            if not auto_failover:
                break

        raise RuntimeError(
            "사용 가능한 AI 공급자가 없거나 모든 활성 공급자에서 한도/일시 오류가 발생했습니다. "
            "L0 정형 명령이나 수동 파일 관리 기능은 계속 정상 동작합니다."
        )

    async def chat_with_provider(
        self,
        *,
        provider_id: str,
        model_id: str | None = None,
        message: str,
        history: list[dict[str, str]] | None = None,
        include_file_context: bool = False,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider_id)
        if not adapter:
            raise SpecValidationError(f"Unknown AI provider: {provider_id}")

        prov_row = await self.db.fetch_one(
            "SELECT * FROM ai_provider_settings WHERE provider_id = ?",
            (provider_id,),
        )
        eff_model = (model_id or "").strip() or (prov_row["model_id"] if prov_row else "default")
        key = await self.get_api_key(provider_id)
        if not key:
            raise ProviderAuthError(
                f"{provider_id} API 키가 설정되지 않았습니다. [AI·Drive 설정]에서 키를 등록하세요.",
                401,
            )

        prefs = await self.db.get_preferences()
        free_only_global = prefs.get("ai_free_only_mode", "false") == "true"
        is_paid = _is_paid_model(provider_id, eff_model)
        if free_only_global and is_paid:
            raise SpecValidationError(
                f"무료 전용 모드가 활성화되어 유료 모델({provider_id}/{eff_model}) 호출이 차단되었습니다. [개인정보·보안]에서 '무료 공급자 전용 모드'를 끄면 사용할 수 있습니다."
            )

        correlation_id = f"chat_{uuid.uuid4().hex[:12]}"
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are a helpful AI assistant inside a mobile file manager & automation workspace. "
                    "Answer concisely in the user's language (Korean by default)."
                ),
            }
        ]
        if include_file_context:
            ctx, _ = await self._build_sanitized_context("local", prefs)
            messages.append(
                {
                    "role": "system",
                    "content": f"Current workspace file metadata: {json.dumps(ctx[:20], ensure_ascii=False)}",
                }
            )
        for h in (history or [])[-12:]:
            if h.get("role") in {"user", "assistant"} and h.get("content"):
                messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": message})

        res = None
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                res = await adapter.chat_completion(key, eff_model, messages)
                break
            except ProviderAuthError as exc:
                await self._record_provider_error(provider_id, "auth_error", str(exc.status_code), str(exc), open_circuit=False)
                raise
            except ProviderTransientError as exc:
                last_exc = exc
                if attempt == 0 and exc.status_code in {500, 502, 503, 504}:
                    await asyncio.sleep(0.5)
                    continue
                state = "rate_limited" if exc.status_code == 429 else "degraded"
                await self._record_provider_error(provider_id, state, str(exc.status_code or "503"), str(exc), open_circuit=True)
                raise RuntimeError(str(exc)) from exc
        if res is None:
            raise RuntimeError(str(last_exc or "AI chat failed."))

        await self._record_usage_event(
            provider_id=provider_id,
            model_id=eff_model,
            request_type="chat",
            token_usage=res.get("usage"),
            status_code=200,
            latency_ms=res["latency_ms"],
            correlation_id=correlation_id,
        )
        return {
            "correlation_id": correlation_id,
            "provider_id": provider_id,
            "model_id": eff_model,
            "reply": res["reply"],
            "latency_ms": res["latency_ms"],
            "usage": res.get("usage"),
        }
