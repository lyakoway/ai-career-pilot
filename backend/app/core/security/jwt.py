"""Access-JWT: HS256, claims sub/iat/exp/jti/locale (SECURITY §2, ADR-020).

Токены и секрет никогда не логируются и не попадают в сообщения ошибок.
Ошибки — доменные (TokenExpiredError / TokenInvalidError), не HTTP.
"""

import time
import uuid
from dataclasses import dataclass

import jwt as pyjwt

from app.core.config import get_settings

__all__ = [
    "AccessPayload",
    "TokenExpiredError",
    "TokenInvalidError",
    "decode_access",
    "encode_access",
]

_REQUIRED_CLAIMS = ("sub", "iat", "exp", "jti", "locale")


@dataclass(frozen=True)
class AccessPayload:
    """Расшифрованные и провалидированные claims access-токена."""

    user_id: uuid.UUID
    locale: str
    jti: str
    iat: int
    exp: int


class TokenExpiredError(Exception):
    """Токен просрочен (exp в прошлом)."""


class TokenInvalidError(Exception):
    """Подпись/структура/claims недействительны (детали не раскрываются)."""


def encode_access(user_id: uuid.UUID, locale: str, ttl: int, *, secret: str | None = None) -> str:
    """Выпустить access-JWT; jti — uuid4 (уникален между вызовами)."""
    key = secret or get_settings().jwt_secret
    now = int(time.time())
    claims = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
        "locale": locale,
    }
    return pyjwt.encode(claims, key, algorithm="HS256")


def decode_access(token: str, *, secret: str | None = None) -> AccessPayload:
    """Проверить подпись (HS256), exp и обязательные claims; доменные ошибки."""
    key = secret or get_settings().jwt_secret
    try:
        claims = pyjwt.decode(
            token,
            key,
            algorithms=["HS256"],
            options={"require": list(_REQUIRED_CLAIMS)},
        )
    except pyjwt.ExpiredSignatureError as exc:
        msg = "access token expired"
        raise TokenExpiredError(msg) from exc
    except pyjwt.PyJWTError as exc:
        msg = "access token invalid"
        raise TokenInvalidError(msg) from exc

    try:
        payload = AccessPayload(
            user_id=uuid.UUID(str(claims["sub"])),
            locale=str(claims["locale"]),
            jti=str(claims["jti"]),
            iat=int(claims["iat"]),
            exp=int(claims["exp"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        msg = "access token invalid"
        raise TokenInvalidError(msg) from exc
    if not payload.locale or not payload.jti:
        msg = "access token invalid"
        raise TokenInvalidError(msg)
    return payload
