from __future__ import annotations

import json
from typing import Any

from backend.db import Database, utc_now_iso
from backend.security.crypto import SecretManager, mask_secret


class EncryptedSecretStore:
    """Stores API keys and OAuth tokens encrypted via Fernet in SQLite (no plaintext secrets)."""

    def __init__(self, db: Database, crypto: SecretManager) -> None:
        self.db = db
        self.crypto = crypto

    async def set_secret(self, key_reference: str, plaintext: str) -> str:
        ciphertext = self.crypto.encrypt(plaintext)
        masked = mask_secret(plaintext)
        await self.db.execute(
            """
            INSERT INTO encrypted_secrets (key_reference, ciphertext, masked_preview, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key_reference) DO UPDATE SET
                ciphertext = excluded.ciphertext,
                masked_preview = excluded.masked_preview,
                updated_at = excluded.updated_at
            """,
            (key_reference, ciphertext, masked, utc_now_iso()),
        )
        return masked

    async def get_secret(self, key_reference: str) -> str | None:
        row = await self.db.fetch_one(
            "SELECT ciphertext FROM encrypted_secrets WHERE key_reference = ?",
            (key_reference,),
        )
        if not row:
            return None
        return self.crypto.decrypt(row["ciphertext"])

    async def get_masked(self, key_reference: str) -> str | None:
        row = await self.db.fetch_one(
            "SELECT masked_preview FROM encrypted_secrets WHERE key_reference = ?",
            (key_reference,),
        )
        return row["masked_preview"] if row else None

    async def delete_secret(self, key_reference: str) -> None:
        await self.db.execute(
            "DELETE FROM encrypted_secrets WHERE key_reference = ?",
            (key_reference,),
        )

    async def set_json_secret(self, key_reference: str, payload: dict[str, Any]) -> None:
        serialized = json.dumps(payload, ensure_ascii=False)
        ciphertext = self.crypto.encrypt(serialized)
        await self.db.execute(
            """
            INSERT INTO encrypted_secrets (key_reference, ciphertext, masked_preview, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key_reference) DO UPDATE SET
                ciphertext = excluded.ciphertext,
                masked_preview = excluded.masked_preview,
                updated_at = excluded.updated_at
            """,
            (key_reference, ciphertext, "oauth_token_set", utc_now_iso()),
        )

    async def get_json_secret(self, key_reference: str) -> dict[str, Any] | None:
        raw = await self.get_secret(key_reference)
        if not raw:
            return None
        return json.loads(raw)
