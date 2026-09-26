"""Encrypting a person's API key before it is stored, and nothing else (7.6).

A user who brings their own model key trusts JobLens with something that can
spend their money. It is stored encrypted with a secret only the server holds
(JOBLENS_SECRET_KEY), so the database -- a backup, a leaked dump, Neon's own
staff -- holds ciphertext and no key. Decrypted only in the moment a model
call is made, never logged (LLMSettings hides `api_key` from its repr), never
sent back to a browser.

**Not built by hand.** CLAUDE.md says build a thing once before using a tool
for it, and cryptography is the exception every security guide agrees on:
home-made encryption fails in ways nobody sees until it is broken. This is
`cryptography`'s Fernet -- AES-128 in CBC mode with an HMAC-SHA256 over it, so
a changed byte is detected rather than decrypted into a wrong key.

Make a secret once, keep it out of git, and do not lose it (a lost secret
means every stored key must be entered again):

    uv run python scripts/db.py new-secret      # then JOBLENS_SECRET_KEY=... in .env
"""

import os
from collections.abc import Mapping

from cryptography.fernet import Fernet, InvalidToken


class VaultError(Exception):
    """A stored key cannot be decrypted with this server's secret."""


class Vault:
    def __init__(self, secret: str):
        try:
            self._fernet = Fernet(secret.encode())
        except ValueError as err:
            raise ValueError(
                "JOBLENS_SECRET_KEY is not a valid key: make one with "
                "`uv run python scripts/db.py new-secret`"
            ) from err

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Vault | None":
        """None when no secret is set: then own keys are simply switched off."""
        secret = (env if env is not None else os.environ).get("JOBLENS_SECRET_KEY")
        return cls(secret) if secret else None

    def lock(self, key: str) -> bytes:
        return self._fernet.encrypt(key.encode())

    def unlock(self, sealed: bytes) -> str:
        try:
            return self._fernet.decrypt(sealed).decode()
        except InvalidToken as err:
            raise VaultError(
                "this key was stored under a different server secret; enter it again"
            ) from err
