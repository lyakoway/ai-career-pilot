"""S1.3: Argon2id-примитивы и политика паролей (SECURITY §2)."""

import pytest
from argon2 import PasswordHasher

from app.core.security.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)

STRONG = "correct-horse-battery-staple-77"


class TestHashVerify:
    def test_hash_is_argon2id_and_roundtrip(self) -> None:
        password_hash = hash_password(STRONG)
        assert password_hash.startswith("$argon2id$")
        assert "m=19456" in password_hash and "t=2" in password_hash and "p=1" in password_hash
        assert verify_password(STRONG, password_hash) is True

    def test_wrong_password_fails(self) -> None:
        assert verify_password("wrong-password-123", hash_password(STRONG)) is False

    def test_malformed_hash_fails_closed(self) -> None:
        assert verify_password(STRONG, "not-a-hash") is False

    def test_hash_does_not_contain_plaintext(self) -> None:
        password_hash = hash_password(STRONG)
        assert STRONG not in password_hash

    def test_needs_rehash_false_for_current_params(self) -> None:
        assert needs_rehash(hash_password(STRONG)) is False

    def test_needs_rehash_true_for_other_params(self) -> None:
        weaker = PasswordHasher(time_cost=3, memory_cost=8192, parallelism=1).hash(STRONG)
        assert needs_rehash(weaker) is True

    def test_needs_rehash_true_for_garbage(self) -> None:
        assert needs_rehash("garbage") is True


class TestPolicy:
    def test_length_boundaries_inclusive(self) -> None:
        validate_password("a" * MIN_PASSWORD_LENGTH)
        validate_password("b" * MAX_PASSWORD_LENGTH)

    @pytest.mark.parametrize(
        "password", ["a" * (MIN_PASSWORD_LENGTH - 1), "c" * (MAX_PASSWORD_LENGTH + 1)]
    )
    def test_length_violations_rejected(self, password: str) -> None:
        with pytest.raises(PasswordPolicyError, match="длина"):
            validate_password(password)

    @pytest.mark.parametrize(
        "password", ["Password123", "QWERTYUIOP", "1234567890", "zxcvbnm12345"]
    )
    def test_denylist_rejected_case_insensitive(self, password: str) -> None:
        with pytest.raises(PasswordPolicyError, match="распространённый"):
            validate_password(password)

    def test_no_composition_requirements(self) -> None:
        # NIST SP 800-63B: без обязательных спецсимволов/классов
        validate_password("толькобуквыпароль")

    def test_error_message_lists_reasons(self) -> None:
        with pytest.raises(PasswordPolicyError) as excinfo:
            validate_password("12345678")  # короткий И из denylist-паттерна
        assert "длина" in str(excinfo.value)
