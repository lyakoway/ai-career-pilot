"""ai_invocations: учёт LLM-вызовов (DATABASE §3.12)

Revision ID: 0002_ai_invocations
Revises: 0001_initial
Create Date: 2026-11-10

Append-only: cp_app получает только SELECT/INSERT (REVOKE UPDATE/DELETE явно) —
стоимость/квоты не зависят от Langfuse и не редактируются приложением.
"""

from alembic import op

revision = "0002_ai_invocations"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_invocations (
            id            uuid PRIMARY KEY,
            user_id       uuid REFERENCES users(id) ON DELETE CASCADE,
            purpose       text NOT NULL,
            provider      text NOT NULL,
            model         text NOT NULL,
            prompt_version text NOT NULL,
            tokens_in     integer NOT NULL,
            tokens_out    integer NOT NULL,
            cost_usd      numeric(10,6) NOT NULL,
            latency_ms    integer NOT NULL,
            trace_id      text,
            status        text NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_ai_invocations_user_time ON ai_invocations (user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_ai_invocations_purpose_time ON ai_invocations (purpose, created_at DESC)"
    )
    op.execute("GRANT SELECT, INSERT ON ai_invocations TO cp_app")
    op.execute("REVOKE UPDATE, DELETE ON ai_invocations FROM cp_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_invocations")
