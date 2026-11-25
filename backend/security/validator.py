from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from pydantic import ValidationError

from backend.models import (
    ALWAYS_CONFIRM_ACTIONS,
    HIGH_RISK_ACTIONS,
    AITaskProposalSpec,
    AllowedAction,
)

_FORBIDDEN_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SHELL_META_CHARS = re.compile(r"[;&|`$><\n\r]")
_STEP_REF_RE = re.compile(r"^\$(previous|step_[1-9]\d*)\.output$")
_VALID_EXT_RE = re.compile(r"^[A-Za-z0-9]{1,15}$")


class SpecValidationError(ValueError):
    """Raised when an AI task specification or file parameter fails security validation."""


def validate_safe_filename(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise SpecValidationError("Filename cannot be empty or '.' / '..'.")
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        raise SpecValidationError("Path traversal characters are not allowed in filename.")
    if _FORBIDDEN_NAME_CHARS.search(cleaned) or _SHELL_META_CHARS.search(cleaned):
        raise SpecValidationError("Filename contains forbidden or shell-special characters.")
    if len(cleaned) > 255:
        raise SpecValidationError("Filename exceeds 255 characters.")
    return cleaned


def validate_safe_extension(ext: str) -> str:
    cleaned = ext.strip().lstrip(".")
    if not _VALID_EXT_RE.match(cleaned):
        raise SpecValidationError("Extension must be 1-15 alphanumeric characters without path separators.")
    return cleaned


def ensure_within_directory(base_dir: Path, target_path: Path) -> Path:
    resolved_base = base_dir.resolve()
    resolved_target = target_path.resolve()
    try:
        resolved_target.relative_to(resolved_base)
    except ValueError as exc:
        raise SpecValidationError("Access outside workspace directory is blocked (path traversal check).") from exc
    return resolved_target


def validate_external_url(url: str, *, allow_localhost: bool = False) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise SpecValidationError("Only http:// and https:// URLs are permitted.")
    if not parsed.netloc or not parsed.hostname:
        raise SpecValidationError("Invalid URL host.")
    host = parsed.hostname.lower()

    # Always block cloud instance metadata endpoints
    if host in {"169.254.169.254", "metadata.google.internal"}:
        raise SpecValidationError("Cloud metadata URLs are blocked.")

    if not allow_localhost:
        if host in {"localhost", "127.0.0.1", "::1"}:
            raise SpecValidationError("Loopback URLs are blocked in production download requests.")
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_link_local or ip.is_private or ip.is_loopback or ip.is_multicast:
                raise SpecValidationError("Private or internal network IP addresses are blocked.")
        except ValueError:
            pass
    return url.strip()


def validate_ai_task_spec(
    raw_spec: dict,
    known_file_ids: Iterable[str],
    *,
    allow_localhost_urls: bool = False,
) -> AITaskProposalSpec:
    """Validates untrusted AI JSON output against strict schema, allowlist, path rules, and known file IDs."""
    try:
        spec = AITaskProposalSpec.model_validate(raw_spec)
    except ValidationError as exc:
        raise SpecValidationError(f"AI JSON failed strict schema validation: {exc}") from exc

    known_set = set(known_file_ids)
    warnings = list(spec.warnings)
    needs_confirm = bool(spec.requires_confirmation)

    for idx, step in enumerate(spec.steps, start=1):
        if step.action not in AllowedAction:
            raise SpecValidationError(f"Action {step.action} is not in allowlist.")

        if step.input_ref:
            if not _STEP_REF_RE.match(step.input_ref):
                raise SpecValidationError(
                    f"Invalid input_ref '{step.input_ref}' at step {idx}; must be '$previous.output' or '$step_N.output'."
                )

        if step.file_id:
            if ".." in step.file_id or "/" in step.file_id or "\\" in step.file_id or _SHELL_META_CHARS.search(step.file_id):
                raise SpecValidationError(f"Forbidden characters in file_id at step {idx}.")
            if step.file_id not in known_set:
                raise SpecValidationError(
                    f"Referenced file_id '{step.file_id}' at step {idx} does not exist in local workspace or Drive cache."
                )

        if step.action in {
            AllowedAction.RENAME_FILE,
            AllowedAction.CHANGE_EXTENSION,
            AllowedAction.COMPRESS_VIDEO,
            AllowedAction.COMPRESS_PDF,
            AllowedAction.DELETE_FILE,
            AllowedAction.OVERWRITE_FILE,
            AllowedAction.UPLOAD_FILE,
        }:
            if not step.file_id and not step.input_ref:
                raise SpecValidationError(f"Step {idx} ({step.action.value}) requires either file_id or input_ref.")

        if step.options:
            if step.options.new_name:
                validate_safe_filename(step.options.new_name)
            if step.options.suffix and _SHELL_META_CHARS.search(step.options.suffix):
                raise SpecValidationError("Suffix contains forbidden characters.")
            if step.options.prefix and _SHELL_META_CHARS.search(step.options.prefix):
                raise SpecValidationError("Prefix contains forbidden characters.")
            if step.options.new_extension:
                validate_safe_extension(step.options.new_extension)
            if step.options.filename:
                validate_safe_filename(step.options.filename)
            if step.options.url:
                validate_external_url(step.options.url, allow_localhost=allow_localhost_urls)

        if step.action == AllowedAction.DOWNLOAD_FILE:
            if not (step.options and step.options.url) and not step.file_id and not step.input_ref:
                raise SpecValidationError("DOWNLOAD_FILE requires options.url, file_id, or input_ref.")

        if step.action == AllowedAction.CHANGE_EXTENSION:
            warnings.append(
                "확장자 변경은 파일명 문자열만 변경하며 파일 내용(바이트)은 변환되지 않습니다. 형식이 오인될 수 있습니다."
            )
            needs_confirm = True

        if step.action in HIGH_RISK_ACTIONS:
            needs_confirm = True

        if step.action in ALWAYS_CONFIRM_ACTIONS:
            warnings.append(f"고위험 작업({step.action.value})은 사용자의 명시적 확인 후에만 실행됩니다.")

    dedup_warnings = list(dict.fromkeys(warnings))
    return spec.model_copy(update={"requires_confirmation": needs_confirm, "warnings": dedup_warnings})
