"""Argon2id-хеширование паролей (SECURITY §2: m=19 MiB, t=2, p=1).

S1.2 — только примитивы hash/verify для provisioning'а владельца;
политика паролей (длина 10–128, denylist) — S1.3 (STAGE_1_PLAN S1.3).
"""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# OWASP-настройки из SECURITY.md §2 (m=19 MiB=19456 KiB, t=2, p=1)
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)

__all__ = ["hash_password", "verify_password"]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
