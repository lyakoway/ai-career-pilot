-- Инфраструктурный bootstrap ролей БД (STAGE_1_PLAN §8.2, ревью v1.2).
-- Выполняется суперпользователем (postgres): при initdb контейнера (docker-entrypoint-initdb.d)
-- и в CI перед миграциями. Идемпотентен. GRANT на таблицы выдаёт миграция 0001.
-- Пароли — dev/CI значения; production задаёт собственные через окружение.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cp_app') THEN
        CREATE ROLE cp_app LOGIN PASSWORD 'cp_app';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cp_migrations') THEN
        CREATE ROLE cp_migrations LOGIN PASSWORD 'cp_migrations';
    END IF;
END
$$;

-- Права уровня схемы: cp_migrations создаёт объекты (DDL), cp_app — только использует.
GRANT USAGE ON SCHEMA public TO cp_app;
GRANT CREATE, USAGE ON SCHEMA public TO cp_migrations;

-- CREATE EXTENSION (citext, миграция 0001) требует CREATE на уровне БАЗЫ,
-- а не только схемы; выдаём cp_migrations на ту базу, к которой применён скрипт.
DO $$
DECLARE db_name text := current_database();
BEGIN
    EXECUTE format('GRANT CREATE ON DATABASE %I TO cp_migrations', db_name);
END
$$;
