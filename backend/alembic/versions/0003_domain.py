"""Домен Stage 2: career_profiles, resumes, profile_facts (DATABASE §3.2–3.3)

Revision ID: 0003_domain
Revises: 0002_ai_invocations
Create Date: 2026-11-10

Схема — строго DATABASE.md:
- career_profiles: partial unique (user_id WHERE is_active) — один активный;
- resumes: uq(user_id, checksum_sha256) — checksum-дедуп; статус-enum;
- profile_facts: CHECK evidence для llm_extraction (AC evidence binding);
  superseded_by — цепочка редактирования; GIN payload.
relocation — jsonb (DATABASE §3.2), не bool (расхождение с текстом ТЗ S2.1
зафиксировано в отчёте). confirmed-факты переживают удаление резюме —
порядок операций в repository.delete_resume (Q-8), не схема.
"""

from alembic import op

revision = "0003_domain"
down_revision = "0002_ai_invocations"
branch_labels = None
depends_on = None

_ENUMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("resume_status", ("uploaded", "parsing", "parsed", "failed")),
    ("fact_type", ("skill", "experience", "project", "education")),
    ("fact_status", ("draft", "confirmed", "rejected", "superseded")),
    ("fact_source", ("llm_extraction", "user_input", "merge")),
)

_DOMAIN_TABLES = "career_profiles, resumes, profile_facts"


def _create_enum(name: str, values: tuple[str, ...]) -> None:
    values_sql = ", ".join(f"'{v}'" for v in values)
    op.execute(f"CREATE TYPE {name} AS ENUM ({values_sql})")


def upgrade() -> None:
    for enum_name, values in _ENUMS:
        _create_enum(enum_name, values)

    op.execute(
        """
        CREATE TABLE career_profiles (
            id               uuid CONSTRAINT pk_career_profiles PRIMARY KEY,
            user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name             text NOT NULL DEFAULT 'Default',
            target_roles     jsonb NOT NULL DEFAULT '[]'::jsonb,
            salary_min       integer,
            salary_max       integer,
            currency         text,
            locations        jsonb,
            work_formats     jsonb,
            employment_types jsonb,
            relocation       jsonb,
            languages        jsonb,
            hard_filters     jsonb NOT NULL DEFAULT '[]'::jsonb,
            settings         jsonb NOT NULL DEFAULT '{}'::jsonb,
            version          integer NOT NULL DEFAULT 1,
            is_active        boolean NOT NULL DEFAULT false,
            activated_at     timestamptz,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Один активный профиль на пользователя: partial unique INDEX
    # (UNIQUE ... WHERE как table-constraint в PG не существует)
    op.execute(
        "CREATE UNIQUE INDEX uq_career_profiles_active ON career_profiles (user_id) WHERE is_active"
    )
    op.execute("CREATE INDEX idx_profiles_user ON career_profiles (user_id)")

    op.execute(
        """
        CREATE TABLE resumes (
            id               uuid CONSTRAINT pk_resumes PRIMARY KEY,
            user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            career_profile_id uuid NOT NULL
                             REFERENCES career_profiles(id) ON DELETE CASCADE,
            file_key         text NOT NULL,
            filename         text,
            mime_type        text,
            size_bytes       integer,
            checksum_sha256  text NOT NULL,
            language         text,
            status           resume_status NOT NULL DEFAULT 'uploaded',
            parse_error      jsonb,
            parsed_at        timestamptz,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_resumes_user_checksum UNIQUE (user_id, checksum_sha256)
        )
        """
    )
    op.execute("CREATE INDEX idx_resumes_user ON resumes (user_id)")
    op.execute("CREATE INDEX idx_resumes_profile ON resumes (career_profile_id)")

    op.execute(
        """
        CREATE TABLE profile_facts (
            id                uuid CONSTRAINT pk_profile_facts PRIMARY KEY,
            user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            career_profile_id uuid NOT NULL
                              REFERENCES career_profiles(id) ON DELETE CASCADE,
            resume_id         uuid REFERENCES resumes(id) ON DELETE CASCADE,
            fact_type         fact_type NOT NULL,
            payload           jsonb NOT NULL,
            evidence          jsonb NOT NULL DEFAULT '[]'::jsonb,
            confidence        numeric(4,3),
            source            fact_source NOT NULL,
            status            fact_status NOT NULL DEFAULT 'draft',
            verified          boolean NOT NULL DEFAULT false,
            verified_at       timestamptz,
            valid_from        date,
            valid_to          date,
            superseded_by     uuid REFERENCES profile_facts(id),
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_facts_evidence_required CHECK (
                source <> 'llm_extraction' OR jsonb_array_length(evidence) > 0
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_facts_profile_type ON profile_facts (career_profile_id, fact_type)"
    )
    op.execute("CREATE INDEX idx_facts_resume ON profile_facts (resume_id)")
    op.execute("CREATE INDEX idx_facts_payload ON profile_facts USING GIN (payload)")

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_DOMAIN_TABLES} TO cp_app")


def downgrade() -> None:
    # Порядок важен: таблицы (зависят от enums) → enums.
    op.execute(f"DROP TABLE IF EXISTS {_DOMAIN_TABLES} CASCADE")
    for enum_name, _values in reversed(_ENUMS):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
