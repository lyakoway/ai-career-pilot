"""S2.1: схема домена — колонки, enums, CHECK-evidence, partial unique, checksum-dedup."""

import asyncio
import uuid

import pytest

from tests.conftest import _truncate, fetch_all, run_write


@pytest.fixture(autouse=True)
def _clean_tables(pg: dict[str, str]) -> None:
    """schema-тесты пишут данные — чистим таблицы перед каждым (autouse)."""
    asyncio.run(_truncate(pg["admin"]))


def _columns(pg: dict[str, str], table: str) -> set[str]:
    rows = fetch_all(
        pg["admin"],
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t",
        {"t": table},
    )
    return {r["column_name"] for r in rows}


def _constraint_exists(pg: dict[str, str], name: str) -> bool:
    rows = fetch_all(
        pg["admin"],
        "SELECT 1 AS x FROM pg_constraint WHERE conname = :n "
        "AND connamespace = 'public'::regnamespace",
        {"n": name},
    )
    return len(rows) == 1


def _enum_values(pg: dict[str, str], enum_name: str) -> set[str]:
    rows = fetch_all(
        pg["admin"],
        "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        "WHERE t.typname = :n",
        {"n": enum_name},
    )
    return {r["enumlabel"] for r in rows}


def test_enums_exist_with_expected_values(pg: dict[str, str]) -> None:
    assert _enum_values(pg, "resume_status") == {"uploaded", "parsing", "parsed", "failed"}
    assert _enum_values(pg, "fact_type") == {"skill", "experience", "project", "education"}
    assert _enum_values(pg, "fact_status") == {"draft", "confirmed", "rejected", "superseded"}
    assert _enum_values(pg, "fact_source") == {"llm_extraction", "user_input", "merge"}


def test_domain_columns_match_database_doc(pg: dict[str, str]) -> None:
    assert _columns(pg, "career_profiles") >= {
        "id",
        "user_id",
        "name",
        "target_roles",
        "salary_min",
        "salary_max",
        "currency",
        "locations",
        "work_formats",
        "employment_types",
        "relocation",
        "languages",
        "hard_filters",
        "settings",
        "version",
        "is_active",
        "activated_at",
        "created_at",
        "updated_at",
    }
    assert _columns(pg, "resumes") >= {
        "id",
        "user_id",
        "career_profile_id",
        "file_key",
        "filename",
        "mime_type",
        "size_bytes",
        "checksum_sha256",
        "language",
        "status",
        "parse_error",
        "parsed_at",
        "created_at",
        "updated_at",
    }
    assert _columns(pg, "profile_facts") >= {
        "id",
        "user_id",
        "career_profile_id",
        "resume_id",
        "fact_type",
        "payload",
        "evidence",
        "confidence",
        "source",
        "status",
        "verified",
        "verified_at",
        "valid_from",
        "valid_to",
        "superseded_by",
        "created_at",
        "updated_at",
    }
    # проверяемые ограничения (partial unique — INDEX, см. миграцию 0003)
    assert _constraint_exists(pg, "ck_facts_evidence_required")
    assert _constraint_exists(pg, "uq_resumes_user_checksum")
    indexes_career = {
        r["indexname"]
        for r in fetch_all(
            pg["admin"], "SELECT indexname FROM pg_indexes WHERE tablename = 'career_profiles'"
        )
    }
    assert "uq_career_profiles_active" in indexes_career


def _create_profile(pg: dict[str, str], user_id: str, email: str) -> uuid.UUID:
    profile_id = uuid.uuid4()
    run_write(
        pg["admin"],
        "INSERT INTO users (id, email, password_hash) VALUES (:u, :e, 'h')",
        {"u": user_id, "e": email},
    )
    run_write(
        pg["admin"],
        "INSERT INTO career_profiles (id, user_id, name) VALUES (:p, :u, 'Default')",
        {"p": profile_id, "u": user_id},
    )
    return profile_id


def test_partial_unique_one_active_profile(pg: dict[str, str]) -> None:
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    profile_a = _create_profile(pg, user_a, "pa@example.com")
    _create_profile(pg, user_b, "pb@example.com")

    # оба активных у одного пользователя → конфликт partial unique
    # (существующий профиль делаем активным, затем INSERT второго активного)
    run_write(
        pg["admin"],
        "UPDATE career_profiles SET is_active = true WHERE id = :p",
        {"p": profile_a},
    )
    with pytest.raises(Exception, match="uq_career_profiles_active"):
        run_write(
            pg["admin"],
            "INSERT INTO career_profiles (id, user_id, is_active) VALUES (:p, :u, true)",
            {"p": uuid.uuid4(), "u": user_a},
        )
    # второй active у другого пользователя — ок
    run_write(
        pg["admin"],
        "INSERT INTO career_profiles (id, user_id, is_active) VALUES (:p, :u, true)",
        {"p": uuid.uuid4(), "u": user_b},
    )
    _ = profile_a


def test_resumes_checksum_unique(pg: dict[str, str]) -> None:
    user_id = uuid.uuid4()
    profile_id = _create_profile(pg, user_id, "pc@example.com")
    run_write(
        pg["admin"],
        "INSERT INTO resumes (id, user_id, career_profile_id, file_key, checksum_sha256) "
        "VALUES (:r, :u, :p, 'users/x/resumes/1/cv.pdf', :c)",
        {"r": uuid.uuid4(), "u": user_id, "p": profile_id, "c": "a" * 64},
    )
    with pytest.raises(Exception, match="uq_resumes_user_checksum"):
        run_write(
            pg["admin"],
            "INSERT INTO resumes (id, user_id, career_profile_id, file_key, checksum_sha256) "
            "VALUES (:r, :u, :p, 'users/x/resumes/2/cv.pdf', :c)",
            {"r": uuid.uuid4(), "u": user_id, "p": profile_id, "c": "a" * 64},
        )


def _insert_fact(
    pg: dict[str, str],
    profile_id: uuid.UUID,
    *,
    source: str,
    evidence: str,
    fact_type: str = "skill",
) -> None:
    run_write(
        pg["admin"],
        "INSERT INTO profile_facts (id, user_id, career_profile_id, fact_type, "
        "payload, evidence, confidence, source) "
        "VALUES (:id, (SELECT user_id FROM career_profiles WHERE id = :p), :p, :t, "
        "CAST(:payload AS jsonb), CAST(:evidence AS jsonb), 0.9, :s)",
        {
            "id": uuid.uuid4(),
            "p": profile_id,
            "t": fact_type,
            "payload": '{"skill": "Python"}',
            "evidence": evidence,
            "s": source,
        },
    )


def test_check_evidence_llm_without_evidence_rejected(pg: dict[str, str]) -> None:
    user_id = uuid.uuid4()
    profile_id = _create_profile(pg, user_id, "pe@example.com")

    # llm_extraction с пустым evidence → CHECK нарушение (AC evidence binding)
    with pytest.raises(Exception, match="ck_facts_evidence_required"):
        _insert_fact(pg, profile_id, source="llm_extraction", evidence="[]")
    # llm_extraction с цитатой → ок
    _insert_fact(
        pg,
        profile_id,
        source="llm_extraction",
        evidence='[{"quote": "Senior Python developer", "section": "experience"}]',
    )
    # user_input без evidence → валидно
    _insert_fact(pg, profile_id, source="user_input", evidence="[]")
