from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from npc_app.database import NpcUser, get_db

security = HTTPBearer(auto_error=False)

NPC_AUTH_SECRET_KEY = os.getenv("NPC_AUTH_SECRET_KEY", "npc-dev-secret-change-me")
NPC_ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("NPC_ACCESS_TOKEN_EXPIRE_HOURS", "168"))
PBKDF2_ITERATIONS = int(os.getenv("NPC_PBKDF2_ITERATIONS", "200000"))


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("utf-8"))


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected_digest = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        ).hex()
        return hmac.compare_digest(digest, expected_digest)
    except Exception:
        return False


def create_access_token(user: NpcUser) -> str:
    header = {"typ": "JWT", "alg": "HS256"}
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=NPC_ACCESS_TOKEN_EXPIRE_HOURS)).timestamp()),
        "aud": "npc_app",
    }

    header_part = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_part = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_part}.{payload_part}".encode()
    signature = hmac.new(NPC_AUTH_SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        header_part, payload_part, signature_part = token.split(".")
        signing_input = f"{header_part}.{payload_part}".encode()
        expected_signature = hmac.new(
            NPC_AUTH_SECRET_KEY.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        actual_signature = _b64url_decode(signature_part)

        if not hmac.compare_digest(expected_signature, actual_signature):
            raise ValueError("invalid signature")

        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
        exp = int(payload.get("exp", 0))
        if exp < int(datetime.now(UTC).timestamp()):
            raise ValueError("token expired")
        if payload.get("aud") != "npc_app":
            raise ValueError("invalid audience")
        return payload
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="NPC 登录状态无效或已过期",
        ) from exc


def authenticate_user(db: Session, username: str, password: str) -> NpcUser:
    user = db.query(NpcUser).filter(NpcUser.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return user


def register_user(db: Session, username: str, password: str) -> NpcUser:
    if db.query(NpcUser).filter(NpcUser.username == username).first():
        raise HTTPException(status_code=400, detail="用户名已存在")

    user = NpcUser(
        username=username,
        password_hash=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> NpcUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="请先登录 NPC 服务")

    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="NPC 登录状态无效")

    user = db.query(NpcUser).filter(NpcUser.id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail="NPC 用户不存在")
    return user
