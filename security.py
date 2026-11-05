"""Credential handling shared by the control plane and workers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def verify_plain_secret(candidate: str, configured: str) -> bool:
    return bool(configured) and hmac.compare_digest(
        candidate.encode("utf-8"), configured.encode("utf-8")
    )


def generate_session_token() -> str:
    return secrets.token_urlsafe(48)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TenantCrypto:
    _fernet: Fernet

    @classmethod
    def from_key(cls, key: str) -> TenantCrypto:
        if not key:
            raise ValueError("TENANT_MASTER_KEY is required")
        try:
            return cls(Fernet(key.encode("ascii")))
        except (ValueError, TypeError) as exc:
            raise ValueError("TENANT_MASTER_KEY must be a valid Fernet key") from exc

    def encrypt_bot_token(self, token: str) -> str:
        if not token:
            raise ValueError("Bot token must not be empty")
        return self._fernet.encrypt(token.encode("utf-8")).decode("ascii")

    def decrypt_bot_token(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError) as exc:
            raise ValueError("Unable to decrypt bot token") from exc


def mask_bot_token(token: str) -> str:
    if not token:
        return ""
    prefix, separator, secret = token.partition(":")
    if not separator:
        return "••••" + token[-4:]
    return f"{prefix}:••••{secret[-4:]}"
