"""Password hashing, token generation, and Fernet encryption for tenant secrets."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from cryptography.fernet import Fernet


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=16384,
        r=8,
        p=1,
        dklen=32,
    )
    return f"scrypt$16384$8$1${base64.b64encode(salt).decode('ascii')}${base64.b64encode(derived).decode('ascii')}"


def verify_password(password: str, encoded: str) -> bool:
    if not encoded or not password:
        return False
    try:
        parts = encoded.split("$")
        if len(parts) != 6 or parts[0] != "scrypt":
            return False
        n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        salt = base64.b64decode(parts[4].encode("ascii"))
        expected = base64.b64decode(parts[5].encode("ascii"))
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(candidate, expected)
    except Exception:
        return False


def verify_plain_secret(candidate: str, configured: str) -> bool:
    if not candidate or not configured:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), configured.encode("utf-8"))


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mask_bot_token(token: str) -> str:
    if not token:
        return ""
    cleaned = token.strip()
    if len(cleaned) <= 10:
        return "***"
    return f"{cleaned[:6]}...{cleaned[-4:]}"


@dataclass(frozen=True)
class TenantCrypto:
    _fernet: Fernet

    @classmethod
    def from_key(cls, key: str | bytes) -> TenantCrypto:
        raw = key.encode("ascii") if isinstance(key, str) else key
        return cls(_fernet=Fernet(raw))

    def encrypt_bot_token(self, token: str) -> str:
        if not token:
            return ""
        return self._fernet.encrypt(token.encode("utf-8")).decode("ascii")

    def decrypt_bot_token(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")

    def encrypt(self, plain_text: str) -> str:
        return self.encrypt_bot_token(plain_text)

    def decrypt(self, cipher_text: str) -> str:
        return self.decrypt_bot_token(cipher_text)
