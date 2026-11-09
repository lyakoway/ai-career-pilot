"""Argon2id-хеширование и политика паролей (SECURITY §2).

Параметры зафиксированы явно (не дефолты библиотеки): m=19456 KiB (19 MiB),
t=2, p=1, hash_len=32 байта, salt_len=16 байт.
Политика: длина 10–128 включительно, denylist частых паролей;
обязательные спецсимволы/композиция НЕ требуются (NIST SP 800-63B).
Ошибки — доменные (PasswordPolicyError), не HTTP.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

__all__ = [
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "PasswordPolicyError",
    "hash_password",
    "needs_rehash",
    "validate_password",
    "verify_password",
]

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128

_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19456,  # 19 MiB
    parallelism=1,
    hash_len=32,
    salt_len=16,
)

# Минимальный denylist (сравнение без учёта регистра); расширение — данными.
_DENYLIST = frozenset(
    {
        "1234567890",
        "1234567899",
        "12345678910",
        "qwertyuiop",
        "qwertyuiop1",
        "qwerty12345",
        "password12",
        "password123",
        "password1234",
        "passw0rd123",
        "iloveyou123",
        "admin12345",
        "administrator1",
        "letmein1234",
        "welcome1234",
        "welcome12345",
        "monkey12345",
        "dragon12345",
        "football123",
        "abc12345678",
        "1111111111",
        "0000000000",
        "aaaaaa1234",
        "abcd123456",
        "qazwsx12345",
        "1q2w3e4r5t6y",
        "zxcvbnm12345",
    }
)


class PasswordPolicyError(ValueError):
    """Нарушение политики пароля (доменная ошибка, не HTTP)."""


def validate_password(password: str) -> None:
    """Проверить политику; PasswordPolicyError с перечнем причин."""
    reasons: list[str] = []
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        reasons.append(
            f"длина должна быть от {MIN_PASSWORD_LENGTH} до {MAX_PASSWORD_LENGTH} символов"
        )
    if password.lower() in _DENYLIST:
        reasons.append("слишком распространённый пароль")
    if reasons:
        raise PasswordPolicyError("; ".join(reasons))


def hash_password(password: str) -> str:
    """Хеш Argon2id (политика проверяется отдельно — см. validate_password)."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Проверить пароль против хеша; битый хеш -> False (не исключение)."""
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except InvalidHashError:
        return False


def needs_rehash(password_hash: str) -> bool:
    """True, если хеш сделан другими параметрами (для ротации параметров)."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True  # неизвестный/битый хеш подлежит замене при следующем входе
