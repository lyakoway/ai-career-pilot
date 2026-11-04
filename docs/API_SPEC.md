# CareerPilot AI — API Specification (проект)

- **Статус:** Draft v1.0 (Stage 0 — проектирование, реализации нет).
- **Стиль:** REST, JSON, OpenAPI 3.1 (генерируется FastAPI; схема — контракт для фронтенда).
- **Связанные:** [ARCHITECTURE.md](ARCHITECTURE.md) · [DATABASE.md](DATABASE.md) · [AI_DESIGN.md](AI_DESIGN.md)

> Полный maшинами-читаемый OpenAPI появится на Stage 1 (`GET /api/openapi.json`). Здесь — проект: список ресурсов, контракты ключевых операций и примеры.

---

## 1. Общие соглашения

| Аспект | Решение |
|---|---|
| Base URL | `/api/v1` |
| Аутентификация | `Authorization: Bearer <access_jwt>` (15 мин) + refresh-ротация; все эндпоинты кроме `/auth/*` и `/health` требуют токен |
| Идентификаторы | UUID v7 в path/теле |
| Пагинация списков | keyset: `?limit=50&cursor=<opaque>`; ответ содержит `next_cursor` |
| Фильтры | query-параметры; множественные значения повтором (`?role=ai_engineer&role=llm_engineer`) |
| Ошибки | единый формат, §8 |
| Идемпотентность | `Idempotency-Key` заголовок для POST, запускающего задачи (`/resumes`, `/copilot/start`, `searches/{id}/run`) |
| Локаль | `Accept-Language: ru|en` — тексты ошибок и генерация; хранится в user |
| Время | ISO 8601 UTC |
| Формат данных | camelCase в JSON API (Pydantic alias) — конвенция фронтенда |

Асинхронные операции возвращают `202 Accepted` + ресурс со статусом; клиент поллит ресурс (или поле `status` в ответе). Примеры статусов: resumes (`parsing` → `parsed`), copilot (`running` → `waiting_user` → `completed`).

---

## 2. Список эндпоинтов

### 2.1 System

| Метод | Путь | Описание |
|---|---|---|
| GET | `/health` | liveness (без auth) |
| GET | `/ready` | readiness: DB, Redis, S3 (без auth) |

### 2.2 Auth & Users (`identity`)

| Метод | Путь | Описание |
|---|---|---|
| POST | `/auth/register` | регистрация email+пароль |
| POST | `/auth/login` | вход → access+refresh |
| POST | `/auth/refresh` | ротация refresh |
| POST | `/auth/logout` | отзыв refresh-сессии |
| PUT | `/auth/password` | смена пароля |
| GET | `/users/me` | профиль пользователя |
| PATCH | `/users/me` | locale, timezone, display_name |
| POST | `/users/me/export` | запрос экспорта данных (async, §6) |
| GET | `/users/me/export/{export_id}` | статус/ссылка на готовый архив (presigned URL) |
| DELETE | `/users/me` | удаление аккаунта (подтверждение паролем) |

### 2.3 Career Profiles (`career_profile`)

| Метод | Путь | Описание |
|---|---|---|
| GET / POST | `/career-profiles` | список / создание |
| GET / PATCH / DELETE | `/career-profiles/{id}` | чтение / изменение / удаление |
| POST | `/career-profiles/{id}/activate` | сделать активным |

### 2.4 Resumes & Facts (`resume_intelligence`)

| Метод | Путь | Описание |
|---|---|---|
| POST | `/resumes` | загрузка файла (multipart), запуск парсинга (202) |
| GET | `/resumes` | список со статусами |
| GET / DELETE | `/resumes/{id}` | статус/метаданные / удаление |
| GET | `/resumes/{id}/file` | presigned URL на файл |
| GET | `/career-profiles/{id}/facts` | факты профиля (`?type=&status=`) |
| POST | `/career-profiles/{id}/facts` | добавить факт вручную |
| PATCH | `/facts/{fact_id}` | подтвердить / изменить / отклонить |
| POST | `/facts/{fact_id}/confirm` | подтвердить как есть |
| POST | `/resumes/{id}/reparse` | повторный парсинг |

### 2.5 Job Searches & Sources (`job_discovery`, `job_sourcing`)

| Метод | Путь | Описание |
|---|---|---|
| GET / POST | `/job-searches` | список / создание подписки |
| GET / PATCH / DELETE | `/job-searches/{id}` | управление |
| POST | `/job-searches/{id}/run` | ручной запуск (202) |
| GET | `/job-searches/{id}/runs` | история прогонов |
| GET | `/sources` | реестр источников + health (read-only) |

### 2.6 Jobs & Matches (`job_dedup`, `job_matching`)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/jobs` | лента вакансий (фильтры: статус, remote, зарплата, язык, поиск; сортировка: score/published) |
| GET | `/jobs/{id}` | карточка вакансии (canonical + postings-источники) |
| GET | `/jobs/{id}/versions` | история изменений |
| POST | `/jobs/{id}/recheck` | ручная верификация свежести (202) |
| GET | `/matches` | матчи активного профиля (`?min_score=&decision=&hard_filter=`) |
| GET | `/matches/{id}` | матч с полным объяснением |
| POST | `/jobs/{id}/match` | рассчитать матч по требованию (202) |
| PATCH | `/matches/{id}/decision` | решение: interested / dismissed / not_interested / will_apply |

### 2.7 Applications & Copilot (`applications`, `copilot`)

| Метод | Путь | Описание |
|---|---|---|
| GET / POST | `/applications` | список / создать черновик (job_id) |
| GET / PATCH / DELETE | `/applications/{id}` | карточка / статусы и поля / архив |
| POST | `/applications/{id}/copilot/start` | запуск workflow (202) |
| GET | `/applications/{id}/copilot/status` | состояние run: нода, статус, вопросы/ревью |
| POST | `/applications/{id}/copilot/resume` | ответ на interrupt / резюме после паузы |
| POST | `/applications/{id}/copilot/cancel` | отмена run |
| GET | `/applications/{id}/documents` | документы (все версии) |
| GET | `/documents/{doc_id}` | версия документа |
| PATCH | `/documents/{doc_id}` | пользовательская правка (новая версия) |
| POST | `/documents/{doc_id}/approve` | одобрение (финализация) |
| POST | `/documents/{doc_id}/regenerate` | перегенерация с фидбеком |
| GET | `/documents/{doc_id}/export?format=md|docx|pdf` | скачивание |
| GET | `/applications/stats` | воронка (counts, конверсии) |

### 2.8 Notifications (`notifications`)

| Метод | Путь | Описание |
|---|---|---|
| POST | `/notifications/telegram/link-code` | одноразовый код привязки |
| GET | `/notifications/channels` | привязанные каналы |
| DELETE | `/notifications/channels/{id}` | отвязать |
| GET / PATCH | `/notifications/settings` | подписки per-type, quiet hours |
| GET | `/notifications` | in-app список |
| POST | `/notifications/{id}/read` | прочитано |

---

## 3. Примеры: Auth

### POST /api/v1/auth/register

```json
// request
{ "email": "user@example.com", "password": "•••••••••••", "locale": "ru" }

// 201
{ "id": "0192a5f0-...", "email": "user@example.com", "locale": "ru",
  "access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer", "expires_in": 900 }
```

### POST /api/v1/auth/login → 200 (тот же формат токенов); POST /api/v1/auth/refresh

```json
// request
{ "refresh_token": "eyJ..." }
// 200 — новая пара; старый refresh отзывается
{ "access_token": "eyJ...", "refresh_token": "eyJ...", "expires_in": 900 }
```

## 4. Примеры: резюме и факты

### POST /api/v1/resumes (multipart/form-data)

```
resume=@cv_ai_engineer.pdf
```

```json
// 202
{ "id": "0192a7c1-...", "status": "parsing", "filename": "cv_ai_engineer.pdf",
  "size_bytes": 241_331, "created_at": "2026-11-03T12:00:00Z" }
```

### GET /api/v1/resumes/{id} — после завершения

```json
{ "id": "0192a7c1-...", "status": "parsed", "language": "en", "parsed_at": "2026-11-03T12:01:40Z",
  "facts_summary": { "total": 87, "draft": 87, "confirmed": 0, "by_type": { "skill": 34, "experience": 6, "project": 9 } } }
```

### GET /api/v1/career-profiles/{id}/facts?type=experience&status=draft

```json
{ "items": [
  { "id": "0192a8e2-...", "fact_type": "experience", "status": "draft", "confidence": 0.93,
    "source": "llm_extraction",
    "payload": { "company": "OOO Romashka", "title": "Senior Frontend Developer",
                 "start": "2022-03", "end": null, "employment": "full_time" },
    "evidence": [ { "resume_id": "0192a7c1-...", "section": "experience",
                    "quote": "Senior Frontend Developer @ Romashka (Mar 2022 — present) ..." } ],
    "verified": false }
],
"next_cursor": null }
```

### PATCH /api/v1/facts/{fact_id} — подтверждение с правкой

```json
// request
{ "action": "confirm", "payload_edits": { "end": "2025-11" } }
// 200
{ "id": "0192a8e2-...", "status": "confirmed", "verified": true, "verified_at": "2026-11-03T12:10:00Z",
  "payload": { "company": "OOO Romashka", "title": "Senior Frontend Developer",
               "start": "2022-03", "end": "2025-11", "employment": "full_time" } }
```

## 5. Примеры: вакансии и матчи

### GET /api/v1/matches?min_score=0.65&hard_filter=passed&limit=2

```json
{ "items": [
  { "id": "0192b1a0-...", "job": { "id": "0192b0ff-...", "title": "LLM Application Engineer",
      "company_name": "Acme AI", "remote_type": "remote", "salary": {"min": 350000, "max": 500000, "currency": "RUB"},
      "published_at": "2026-11-01T09:00:00Z", "best_source_url": "https://..." },
    "final_score": 0.81, "hard_filter_passed": true,
    "components": { "skills": 0.82, "experience": 0.9, "semantics": 0.76, "logistics": 1.0 },
    "skill_gaps": [ {"requirement": "Kubernetes в проде", "critical": true} ],
    "decision": "none", "computed_at": "2026-11-03T13:00:00Z" }
],
"next_cursor": "Mg" }
```

### GET /api/v1/matches/{id} — фрагмент объяснения

```json
{ "id": "0192b1a0-...", "final_score": 0.81, "hard_filter_passed": true,
  "explanation": {
    "summary": "Сильный матч: 8/10 обязательных требований закрыты",
    "evidence": [
      { "requirement": "Опыт production RAG", "verdict": "covered",
        "fact_ids": ["0192a8e5-..."], "evidence_quote": "built hybrid retrieval (BM25+pgvector), deflection +35%" },
      { "requirement": "Kubernetes", "verdict": "gap", "critical": true,
        "hint": "в профиле нет подтверждённого факта; есть Docker (fact 0192a8f0-...)" }
    ] },
  "job": { "...": "полная карточка" } }
```

## 6. Примеры: Application Copilot

### POST /api/v1/applications

```json
// request
{ "job_id": "0192b0ff-...", "source": "copilot" }
// 201
{ "id": "0192c000-...", "job_id": "0192b0ff-...", "status": "draft", "created_at": "..." }
```

### POST /api/v1/applications/{id}/copilot/start → 202

```json
{ "run_id": "0192c001-...", "status": "running", "current_node": "job_analyzer" }
```

### GET /api/v1/applications/{id}/copilot/status — граф дошёл до request_user_input

```json
{ "run_id": "0192c001-...", "status": "waiting_user", "current_node": "request_user_input",
  "await": {
    "kind": "questions",
    "questions": [
      { "id": "q1", "question": "Есть ли у вас опыт выравнивания LLM (LoRA/DPO) на своих проектах?",
        "why": "Вакансия упоминает fine-tuning как обязательное требование",
        "related_gap": "ML fine-tuning", "optional": true },
      { "id": "q2", "question": "Какой самый заметный измеримый эффект дал ваш RAG-проект?",
        "why": "Усилит резюме и cover letter", "related_gap": null, "optional": true }
    ]
  },
  "progress": { "nodes_done": ["job_analyzer", "profile_matcher", "gap_analyzer"] } }
```

### POST /api/v1/applications/{id}/copilot/resume — ответ пользователя

```json
// request
{ "answers": [ { "id": "q1", "answer": "Да, LoRA на Llama-3 для классификации тикетов, +12% accuracy",
                 "confirm_fact": true },
               { "id": "q2", "answer": "deflection 35% за квартал", "confirm_fact": true } ] }
// 202
{ "run_id": "0192c001-...", "status": "running", "current_node": "resume_generator" }
```

`confirm_fact: true` — ответ сохраняется как новый `profile_fact` (source=user_input, verified), переиспользуется в дальнейших откликах.

### GET .../copilot/status — граф дошёл до human_review

```json
{ "status": "waiting_user", "current_node": "human_review",
  "await": {
    "kind": "review",
    "documents": [
      { "doc_id": "0192d001-...", "kind": "resume", "version": 1, "review_status": "draft",
        "fact_check": { "supported": 41, "unsupported": 0, "unverifiable": 2,
          "flags": [ {"claim": "увеличил конверсию откликов", "verdict": "unverifiable", "note": "нет измеримого факта в профиле"} ] } },
      { "doc_id": "0192d002-...", "kind": "cover_letter", "version": 1 },
      { "doc_id": "0192d003-...", "kind": "answers", "version": 1 }
    ] },
  "progress": { "nodes_done": ["...генераторы", "fact_checker"] } }
```

### POST /api/v1/documents/{doc_id}/approve → 200; итог workflow

```json
{ "application_id": "0192c000-...", "status": "completed",
  "application_status": "materials_ready",
  "documents": [ { "kind": "resume", "version": 1, "review_status": "approved" },
                 { "kind": "cover_letter", "version": 2, "review_status": "approved" } ] }
```

### Экспорт: GET /api/v1/documents/{doc_id}/export?format=docx → 302 на presigned URL (TTL 15 мин)

## 7. Примеры: остальные ресурсы

### POST /api/v1/job-searches

```json
// request
{ "name": "AI/LLM Engineer, remote",
  "query": { "keywords": ["AI Engineer", "LLM Engineer", "AI Application Engineer"],
             "sources": ["arbeitnow", "remotive", "adzuna", "hn_algolia"],
             "remote_only": true },
  "schedule": { "interval_minutes": 120 } }
// 201
{ "id": "0192e000-...", "is_active": true, "next_run_at": "2026-11-03T14:00:00Z", "...": "..." }
```

### POST /api/v1/notifications/telegram/link-code

```json
// 201
{ "code": "CP-8F3K2Q", "expires_in": 600,
  "bot_username": "careerpilot_bot", "instructions": "Отправьте код боту в Telegram" }
```

### POST /api/v1/users/me/export → 202

```json
{ "export_id": "0192f000-...", "status": "processing",
  "note": "Готовый архив будет доступен по GET /users/me/export/{export_id} (presigned URL)" }
```

### Конечный автомат application.status

```
draft ──start copilot──▶ awaiting_input ──answers──▶ (генерация) ──approval──▶ materials_ready
materials_ready ──пользователь отправил──▶ submitted
submitted ─▶ interview ─▶ offer
submitted/interview/offer ─▶ rejected | withdrawn
любой ─▶ archived
```

## 8. Формат ошибок

```json
// 404
{ "error": { "code": "not_found", "message": "Resume not found",
             "details": {"resource": "resume"}, "request_id": "0192aa01-..." } }
// 422 (бизнес-правило)
{ "error": { "code": "domain_rule_violation",
             "message": "Cannot transition application from 'offer' to 'submitted'",
             "details": {"from": "offer", "to": "submitted"}, "request_id": "..." } }
// 429
{ "error": { "code": "rate_limited", "message": "Too many copilot runs",
             "details": {"retry_after_seconds": 3600}, "request_id": "..." } }
```

Каталог кодов: `validation_error`, `unauthorized`, `forbidden`, `not_found`, `conflict`, `domain_rule_violation`, `rate_limited`, `external_service_error`, `internal_error`. Сообщения локализуются по `Accept-Language` (ru/en), `code` стабилен для фронтенда.

## 9. Версионирование и совместимость

- Несовместимые изменения — новый префикс `/api/v2`; в пределах v1 — только additive-изменения (новые поля/эндпоинты).
- Deprecation: заголовок `Deprecation: true` + `Sunset` (RFC 8594), минимум 2 месяца поддержки.
- Webhooks (Telegram → API) не входят в публичный контракт: внутренний механизм, формат зависит от Bot API.

## 10. Замечания для Stage 1

- OpenAPI-схема фронтенд получает через codegen (`openapi-typescript`) — ручных типов на фронтенде быть не должно.
- Все ответы списков — единая обёртка `{items, next_cursor}`.
- CORS: только origin Dashboard; для personal-деплоя допустим same-origin (nginx проксирует `/api`).
- Rate limiting API: per-user (Redis, token bucket) — базовые пределы Stage 8, copilot-лимиты раньше (стоимость LLM).
