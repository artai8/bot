import os
import time
import hashlib
import secrets
import logging

logger = logging.getLogger(__name__)

# Web UI 管理密码
ADMIN_PASSWORD = os.environ.get("WEB_ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.environ.get("WEB_SECRET_KEY", secrets.token_hex(32))
TOKEN_EXPIRE = int(os.environ.get("WEB_TOKEN_EXPIRE", "86400"))


class AuthManager:
    def __init__(self):
        self.active_tokens = {}

    def _hash_password(self, password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()

    def verify_password(self, password: str) -> bool:
        return password == ADMIN_PASSWORD

    def generate_token(self) -> str:
        token = secrets.token_hex(32)
        self.active_tokens[token] = {
            'created_at': time.time(),
            'expires_at': time.time() + TOKEN_EXPIRE
        }
        return token

    def verify_token(self, token: str) -> bool:
        if not token:
            return False
        token = token.replace("Bearer ", "")
        if token not in self.active_tokens:
            return False
        token_data = self.active_tokens[token]
        if time.time() > token_data['expires_at']:
            del self.active_tokens[token]
            return False
        return True

    def revoke_token(self, token: str):
        token = token.replace("Bearer ", "")
        self.active_tokens.pop(token, None)

    def cleanup_expired(self):
        now = time.time()
        expired = [t for t, d in self.active_tokens.items() if now > d['expires_at']]
        for t in expired:
            del self.active_tokens[t]


auth_manager = AuthManager()
