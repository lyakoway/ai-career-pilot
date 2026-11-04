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
| [docs/DECISIONS.md](docs/DECISIONS.md) | Архитектурные решения (ADR-001…019) |

## Планируемый стек

Python 3.12 · FastAPI · SQLAlchemy 2 · PostgreSQL 16 + pgvector · Celery + Redis · LangGraph · Langfuse (fail-open observability) · S3/MinIO · React + TypeScript + Vite · Docker Compose.

## License

Не определена (см. ADR-процесс в docs/DECISIONS.md).
