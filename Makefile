# CareerPilot AI — шорткаты разработки. Python-команды выполняются через uv в backend/.

.PHONY: help install lock lint fmt type test up down ps

help: ## Показать список целей
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-8s %s\n", $$1, $$2}'

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
