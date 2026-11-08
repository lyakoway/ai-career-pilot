"""CLI создания владельца (personal MVP; SECURITY §12, ADR-021).

Идемпотентен по email: существующий email -> отказ с диагностикой (exit 1).
Пишет audit user.provisioned (context без секретов/PII). К API не подключён.

Использование (из backend/):
    uv run python scripts/provision_owner.py --email owner@example.com --password '...'
Пароль можно передать через CP_OWNER_PASSWORD (аргумент приоритетнее).
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.core.db import create_db_engine, create_session_factory
from app.core.logging import configure_logging
from app.core.security.passwords import hash_password
from app.modules.identity import repository


async def provision(email: str, password: str) -> int:
    settings = get_settings()
    configure_logging(settings)
    engine = create_db_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            existing = await repository.get_user_by_email(session, email)
            if existing is not None:
                print(f"ОШИБКА: пользователь {email} уже существует (id={existing.id})")
                return 1
            user = await repository.create_user(
                session, email=email, password_hash=hash_password(password)
            )
            await repository.append_audit(
                session,
                user_id=user.id,
                action="user.provisioned",
                resource_type="user",
                resource_id=user.id,
                context={"via": "cli"},
            )
            await session.commit()
            print(f"OK: владелец {email} создан (id={user.id}); audit: user.provisioned")
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Создание владельца CareerPilot AI")
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--password",
        default=None,
        help="Пароль; если не указан — берётся из CP_OWNER_PASSWORD",
    )
    args = parser.parse_args()
    import os

    password = args.password or os.environ.get("CP_OWNER_PASSWORD", "")
    if not password:
        print("ОШИБКА: пароль не задан (--password или CP_OWNER_PASSWORD)")
        return 2
    return asyncio.run(provision(args.email, password))


if __name__ == "__main__":
    raise SystemExit(main())
