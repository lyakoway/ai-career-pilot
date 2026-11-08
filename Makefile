# CareerPilot AI — шорткаты разработки. Python-команды выполняются через uv в backend/.

DB_ADMIN_URL ?= postgresql://postgres:postgres@localhost:5432/careerpilot
CP_MIGRATIONS_DATABASE_URL ?= postgresql+asyncpg://cp_migrations:cp_migrations@localhost:5432/careerpilot

.PHONY: help install lock lint fmt type test up down ps bootstrap migrate migrate-down provision-owner

help: ## Показать список целей
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

install: ## Установить зависимости (uv sync, включая dev)
	cd backend && uv sync

lock: ## Пересоздать uv.lock
	cd backend && uv lock

lint: ## ruff check + format --check
	cd backend && uv run ruff check . && uv run ruff format --check .

fmt: ## ruff format + autofix
	cd backend && uv run ruff format . && uv run ruff check --fix .

type: ## mypy (app, strict)
	cd backend && uv run mypy

test: ## pytest
	cd backend && uv run pytest

up: ## docker compose up -d (api/db/redis, с build)
	docker compose -f docker/docker-compose.yml up -d --build

down: ## docker compose down
	docker compose -f docker/docker-compose.yml down

ps: ## Статус сервисов compose
	docker compose -f docker/docker-compose.yml ps

bootstrap: ## Накатить bootstrap.sql (роли cp_app/cp_migrations) на запущенный db
	psql "$(DB_ADMIN_URL)" -f backend/docker/db/bootstrap.sql

migrate: ## alembic upgrade head (под ролью cp_migrations)
	cd backend && CP_MIGRATIONS_DATABASE_URL=$(CP_MIGRATIONS_DATABASE_URL) uv run alembic upgrade head

migrate-down: ## alembic downgrade base (под ролью cp_migrations)
	cd backend && CP_MIGRATIONS_DATABASE_URL=$(CP_MIGRATIONS_DATABASE_URL) uv run alembic downgrade base

provision-owner: ## Создать владельца: make provision-owner EMAIL=... [PASSWORD=...]
	cd backend && uv run python scripts/provision_owner.py --email $(EMAIL) $(if $(PASSWORD),--password '$(PASSWORD)',)
