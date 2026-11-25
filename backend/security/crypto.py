from __future__ import annotations

import base64
import hashlib
import logging
import re
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretManager:
    """Encrypts and decrypts user-supplied API keys and OAuth tokens at rest using Fernet."""

    def __init__(self, configured_secret: str, data_dir: Path) -> None:
        self.using_ephemeral_secret = False
        secret = (configured_secret or "").strip()

        if not secret or secret == "change-this-to-a-strong-random-secret-in-production":
            key_file = data_dir / ".secret_key"
            data_dir.mkdir(parents=True, exist_ok=True)
            if key_file.exists():
                secret = key_file.read_text(encoding="utf-8").strip()
            else:
                secret = Fernet.generate_key().decode("ascii")
                key_file.write_text(secret, encoding="utf-8")
            self.using_ephemeral_secret = True

        self._fernet = Fernet(self._derive_fernet_key(secret))

    @staticmethod
    def _derive_fernet_key(secret: str) -> bytes:
        try:
            raw = secret.encode("ascii")
            Fernet(raw)
            return raw
        except Exception:
            digest = hashlib.sha256(secret.encode("utf-8")).digest()
            return base64.urlsafe_b64encode(digest)

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Stored secret could not be decrypted with current ENCRYPTION_SECRET.") from exc


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    stripped = value.strip()
    if len(stripped) <= 8:
        return "••••"
    return f"{stripped[:4]}••••{stripped[-4:]}"


_REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-_\.=]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"\bsk-or-v1-[A-Za-z0-9\-_]{10,}"), "[REDACTED_OPENROUTER_KEY]"),
    (re.compile(r"\bsk-[A-Za-z0-9\-_]{12,}"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}"), "[REDACTED_GEMINI_KEY]"),
    (re.compile(r"(x-goog-api-key['\":\s=]+)[^\s'\",}]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"((?:access_token|refresh_token|client_secret|api_key)['\":\s=]+)[^\s'\",}&]+", re.IGNORECASE), r"\1[REDACTED]"),
]


def redact_sensitive_text(text: str) -> str:
    result = text
    for pattern, replacement in _REDACTION_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_text(record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sensitive_text(arg) if isinstance(arg, str) else arg for arg in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: redact_sensitive_text(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
        return True
