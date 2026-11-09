"""S1.3: access-JWT (HS256, claims sub/iat/exp/jti/locale; SECURITY §2, ADR-020)."""

import time
import uuid

import jwt as pyjwt
import pytest

from app.core.security.jwt import (
    TokenExpiredError,
    TokenInvalidError,
    decode_access,
    encode_access,
)

SECRET = "x" * 32
OTHER_SECRET = "y" * 32


def test_round_trip() -> None:
    user_id = uuid.uuid4()
    token = encode_access(user_id, "en", 900, secret=SECRET)
    payload = decode_access(token, secret=SECRET)
    assert payload.user_id == user_id
    assert payload.locale == "en"
    assert payload.exp - payload.iat == 900
    assert payload.jti


def test_jti_unique_between_calls() -> None:
    user_id = uuid.uuid4()
    token_a = encode_access(user_id, "ru", 900, secret=SECRET)
    token_b = encode_access(user_id, "ru", 900, secret=SECRET)
    assert decode_access(token_a, secret=SECRET).jti != decode_access(token_b, secret=SECRET).jti


def test_expired_token() -> None:
    token = encode_access(uuid.uuid4(), "ru", -10, secret=SECRET)
    with pytest.raises(TokenExpiredError):
        decode_access(token, secret=SECRET)


def test_wrong_signature() -> None:
    token = encode_access(uuid.uuid4(), "ru", 900, secret=SECRET)
    with pytest.raises(TokenInvalidError):
        decode_access(token, secret=OTHER_SECRET)


def test_tampered_payload() -> None:
    token = encode_access(uuid.uuid4(), "ru", 900, secret=SECRET)
    header, _, signature = token.split(".")
    claims = pyjwt.decode(token, SECRET, algorithms=["HS256"])
    claims["sub"] = str(uuid.uuid4())  # подмена субъекта
    forged = (
        header
        + "."
        + pyjwt.encode(claims, SECRET, algorithm="HS256").split(".")[1]
        + "."
        + signature
    )
    with pytest.raises(TokenInvalidError):
        decode_access(forged, secret=SECRET)


def test_missing_required_claim() -> None:
    now = int(time.time())
    claims = {"sub": str(uuid.uuid4()), "iat": now, "exp": now + 900, "locale": "ru"}
    token = pyjwt.encode(claims, SECRET, algorithm="HS256")  # без jti
    with pytest.raises(TokenInvalidError):
        decode_access(token, secret=SECRET)


def test_non_uuid_sub_rejected() -> None:
    now = int(time.time())
    claims = {
        "sub": "not-a-uuid",
        "iat": now,
        "exp": now + 900,
        "jti": "j",
        "locale": "ru",
    }
    token = pyjwt.encode(claims, SECRET, algorithm="HS256")
    with pytest.raises(TokenInvalidError):
        decode_access(token, secret=SECRET)


def test_garbage_token() -> None:
    with pytest.raises(TokenInvalidError):
        decode_access("garbage.token.value", secret=SECRET)
