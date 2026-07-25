"""
Core security primitives: password hashing + JWT issuance/verification.

Design decision: the ACCESS token embeds hospital_id, role, and a flattened
list of permission codes directly in its claims. This makes authorization
checks on every request a pure in-memory operation (decode + check a set)
instead of a DB round trip per API call -- this is what lets the
authorization layer scale horizontally without hammering the DB.

The REFRESH token is opaque and only ever exchanged for a new access token;
its hash is stored server-side so it can be revoked (logout / compromised
device) even though access tokens themselves cannot be revoked before they
expire (hence the short 15 min lifetime).
"""
from datetime import datetime, timedelta, timezone
from typing import Any
import base64
import hashlib
import hmac
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from jose import jwt, JWTError
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    *,
    user_id: str,
    hospital_id: str | None = None,
    admin_organization_id: str | None = None,
    role: str,
    permissions: list[str],
    user_type: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {
        "sub": user_id,
        "hospital_id": hospital_id,
        "admin_organization_id": admin_organization_id,
        "role": role,
        "permissions": permissions,
        "user_type": user_type or ("admin" if admin_organization_id else "clinical"),
        "type": "access",
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc


def generate_refresh_token() -> tuple[str, str, datetime]:
    """Returns (raw_token_to_send_to_client, sha256_hash_to_store_in_db, expires_at)."""
    raw_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return raw_token, token_hash, expires_at


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def generate_invitation_token() -> tuple[str, str]:
    """Return a one-time raw invitation token and the SHA-256 value stored in the DB."""
    raw_token = secrets.token_urlsafe(48)
    return raw_token, hashlib.sha256(raw_token.encode()).hexdigest()


def hash_invitation_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def encrypt_workspace_id(hospital_id: str) -> str:
    """Return a stable, authenticated, URL-safe encryption of a tenant UUID."""
    plaintext = hospital_id.encode("utf-8")
    key = hashlib.sha256(
        f"{settings.JWT_SECRET_KEY}:doctor-workspace".encode("utf-8")
    ).digest()
    nonce = hmac.new(key, plaintext, hashlib.sha256).digest()[:12]
    encrypted = AESGCM(key).encrypt(nonce, plaintext, b"doctor-workspace")
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii").rstrip("=")


def decrypt_workspace_id(encrypted_id: str) -> str:
    key = hashlib.sha256(
        f"{settings.JWT_SECRET_KEY}:doctor-workspace".encode("utf-8")
    ).digest()
    padded = encrypted_id + "=" * (-len(encrypted_id) % 4)
    try:
        value = base64.urlsafe_b64decode(padded.encode("ascii"))
        plaintext = AESGCM(key).decrypt(
            value[:12], value[12:], b"doctor-workspace"
        )
        return plaintext.decode("utf-8")
    except (InvalidTag, ValueError, TypeError) as exc:
        raise ValueError("Invalid workspace identifier") from exc
