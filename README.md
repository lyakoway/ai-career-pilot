# CareerPilot AI

AI-powered Career Assistant: агрегация вакансий из разрешённых источников, объяснимый матчинг с профилем, подготовка материалов отклика (адаптированное резюме, cover letter, ответы) с проверкой фактической достоверности. Принцип продукта — **Prepare, not Send**: система готовит, человек проверяет и отправляет сам.

**Статус:** Stage 0 (проектирование) завершён. Реализация кода ещё не начата — см. [docs/ROADMAP.md](docs/ROADMAP.md), Stage 1.

## Документация

| Документ | Содержание |
|---|---|
| [docs/PRD.md](docs/PRD.md) | Продуктовые требования, сценарии, риски |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Модульный монолит, стек, границы модулей |
| [docs/DATABASE.md](docs/DATABASE.md) | Схема БД (27 таблиц), multi-tenancy, retention |
| [docs/API_SPEC.md](docs/API_SPEC.md) | REST API `/api/v1`, примеры |
| [docs/AI_DESIGN.md](docs/AI_DESIGN.md) | AI-компоненты, LangGraph workflow, защита от инъекций |
| [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) | Источники вакансий, Source Adapter contract |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Стратегия evaluation, метрики, датасеты |
| [docs/SECURITY.md](docs/SECURITY.md) | Безопасность, privacy, изоляция данных |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Этапы разработки Stage 1–8 |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Архитектурные решения (ADR-001…021) |
| [docs/STAGE_0_1_AUDIT.md](docs/STAGE_0_1_AUDIT.md) | Аудит согласованности Stage 0.1: противоречия, исправления, открытые вопросы |

## Планируемый стек

Python 3.12 · FastAPI · SQLAlchemy 2 · PostgreSQL 16 + pgvector · Celery + Redis · LangGraph · Langfuse (fail-open observability) · S3/MinIO · React + TypeScript + Vite · Docker Compose.

## Backend: запуск и проверка (Stage 1)

### Требования

- Docker с Compose v2.24+ (compose-файл использует `env_file.required: false`);
- [uv](https://docs.astral.sh/uv/) (менеджер зависимостей; Python 3.12 ставится автоматически);
- `psql` (клиент PostgreSQL) — для `make bootstrap`.

### Быстрый старт

```bash
cp backend/.env.example backend/.env      # секреты для локальной разработки
make install                              # uv sync (зависимости + dev)
make up                                   # api + db (postgres:16) + redis, с healthchecks
make ps                                   # все сервисы должны быть healthy
make bootstrap                            # роли cp_app / cp_migrations (bootstrap.sql)
make migrate                              # alembic upgrade head под cp_migrations
make provision-owner EMAIL=owner@example.com PASSWORD='Str0ng-Passw0rd!'
```

Пароль для `provision-owner` можно передать и через `CP_OWNER_PASSWORD` (аргумент приоритетнее). Скрипт идемпотентен по email: повторный запуск откажет с диагностикой (exit 1); слабый пароль — exit 2.

### Проверки curl

```bash
# liveness / readiness (без auth)
curl -s http://localhost:8000/health          # {"status":"ok"}
curl -s http://localhost:8000/ready           # {"status":"ok","checks":{"database":true,"redis":true}}

# login (access в теле, refresh в httpOnly Set-Cookie cp_refresh)
curl -i -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"owner@example.com","password":"Str0ng-Passw0rd!"}'
# → 200 {"id":"...","accessToken":"eyJ...","tokenType":"bearer","expiresIn":900}
#   + Set-Cookie: cp_refresh=rt_...; HttpOnly; Secure; SameSite=lax; Path=/api/v1/auth

# refresh (cookie-режим; Origin обязателен — CSRF-guard)
curl -i -X POST http://localhost:8000/api/v1/auth/refresh \
  -H 'Origin: http://localhost:5173' \
  -b 'cp_refresh=rt_...'
# → 200 {"accessToken":"..."} + новая cp_refresh-cookie
#   повтор старым токеном → 401 token_reuse_detected (все сессии отозваны)

# свой профиль (Bearer access из login/refresh)
curl -s http://localhost:8000/api/v1/users/me -H 'Authorization: Bearer eyJ...'
# → {"id":"...","email":"...","displayName":null,"locale":"ru","timezone":"...","createdAt":"...","updatedAt":"..."}
```

### Команды разработки

| Команда | Что делает |
|---|---|
| `make lint` | `ruff check` + `ruff format --check` |
| `make fmt` | `ruff format` + autofix |
| `make type` | `mypy --strict` (app/) |
| `make test` | pytest: unit + integration (нужна PG, см. ниже) |
| `make openapi` | детерминированная генерация `backend/openapi.json` |
| `make logs` / `make ps` / `make down` | логи / статус / остановка compose |
| `make migrate-down` | откат миграций до base (осторожно: схема удаляется) |

### Интеграционные тесты без Docker

Если Docker недоступен, поднимите локальные PostgreSQL 16 и Redis и укажите URL:

```bash
export CP_TEST_DATABASE_URL="postgresql://postgres@127.0.0.1:5434/careerpilot_test"
# опционально (выводятся из админского URL, если не заданы):
#   CP_TEST_APP_DATABASE_URL         — роль cp_app (реальные GRANT-тесты)
#   CP_TEST_MIGRATIONS_DATABASE_URL  — роль cp_migrations (миграции)
```

Тесты сами применяют `bootstrap.sql`, приводят базу к head и чистят таблицы между тестами. Конвенция проекта: локальный тестовый PG — порт **5434** (5433 занят соседним проектом), кластер в `backend/.testpg` (gitignored), бинарии [EDB](https://www.enterprisedb.com/download-postgresql-binaries) в `/tmp/pg16`. В CI используются services-контейнеры (postgres:16 + redis:7), эти переменные задаются в workflow.

## License

Не определена (см. ADR-процесс в docs/DECISIONS.md).
