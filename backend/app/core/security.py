"""Authentication, RBAC, secret hashing/encryption. No third-party auth service required."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import User
from app.db.session import get_db

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},
    "auditor": {
        "document:read", "document:write", "claim:read", "claim:write", "graph:read",
        "contradiction:read", "contradiction:confirm", "decision:read", "decision:write",
        "decision:submit", "analysis:run", "report:read", "report:generate", "review:read",
        "benchmark:read", "benchmark:run", "provider:read", "audit:read", "qa:ask",
    },
    "reviewer": {
        "document:read", "claim:read", "claim:review", "graph:read", "contradiction:read",
        "contradiction:confirm", "decision:read", "decision:approve", "review:read",
        "analysis:run", "report:read", "report:generate", "audit:read", "qa:ask",
        "benchmark:read", "provider:read",
    },
    "viewer": {
        "document:read", "claim:read", "graph:read", "contradiction:read", "decision:read",
        "report:read", "review:read", "benchmark:read", "audit:read",
    },
}
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_access_token(user: User) -> str:
    s = get_settings()
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps({
        "sub": user.id, "ws": user.workspace_id, "role": user.role,
        "email": user.email, "iat": now, "exp": now + s.jwt_ttl_minutes * 60,
    }, separators=(",", ":")).encode())
    sig = _b64(hmac.new(s.resolved_secret().encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def decode_token(token: str) -> dict[str, Any]:
    s = get_settings()
    try:
        header, payload, sig = token.split(".")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Malformed token") from exc
    expected = _b64(hmac.new(s.resolved_secret().encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail="Invalid token signature")
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token payload") from exc
    if int(data.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="Token expired")
    return data


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    data = decode_token(credentials.credentials)
    user = db.get(User, data.get("sub", ""))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User is not active")
    if user.workspace_id != data.get("ws"):
        raise HTTPException(status_code=403, detail="Workspace mismatch")
    return user


def require_roles(*roles: str):
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail=f"Requires role: {', '.join(roles)}")
        return user
    return _dep


def require_permission(permission: str):
    def _dep(user: User = Depends(get_current_user)) -> User:
        perms = ROLE_PERMISSIONS.get(user.role, set())
        if "*" not in perms and permission not in perms:
            raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")
        return user
    return _dep


def _fernet() -> Fernet:
    key = hashlib.sha256(f"ega-secret-v1:{get_settings().resolved_secret()}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="Stored credential cannot be decrypted with current key") from exc


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "********"
    return f"{value[:4]}…{value[-4:]}"


def assert_same_workspace(user: User, workspace_id: str) -> None:
    if user.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail="Cross-workspace access denied")
