# CareerPilot AI — API Specification (проект)

- **Статус:** Draft v1.1 (Stage 0.1 — аудит согласованности: исправлены модель токенов, camelCase-примеры, идемпотентность, FSM, контракт экспорта).
- **Стиль:** REST, JSON, OpenAPI 3.1 (генерируется FastAPI; схема — контракт для фронтенда).
- **Связанные:** [ARCHITECTURE.md](ARCHITECTURE.md) · [DATABASE.md](DATABASE.md) · [AI_DESIGN.md](AI_DESIGN.md) · [SECURITY.md](SECURITY.md)

> Полный машиночитаемый OpenAPI появится на Stage 1 (`GET /api/openapi.json`). Здесь — проект: список ресурсов, контракты ключевых операций и примеры.

---

## 1. Общие соглашения

| Аспект | Решение |
|---|---|
| Base URL | `/api/v1` |
| Идентификаторы | UUID v7 в path/теле |
| Пагинация списков | keyset: `?limit=50&cursor=<opaque>`; ответ содержит `nextCursor` |
| Фильтры | query-параметры; множественные значения повтором (`?role=aiEngineer&role=llmEngineer`) |
| Ошибки | единый формат, §8 |
| Идемпотентность | заголовок `Idempotency-Key` для POST, запускающего задачи (§1.2) |
| Локаль | `Accept-Language: ru\|en` — тексты ошибок и генерация; хранится в user |
| Время | ISO 8601 UTC |
| Формат данных | **camelCase** во всех JSON API (Pydantic alias) — конвенция фронтенда. Внутренние структуры БД и graph state остаются snake_case |

### 1.1 Аутентификация и модель токенов

**Access-токен** — JWT (HS256, TTL 15 мин, claims: `sub`, `iat`, `exp`, `jti`, `locale`). Передаётся как `Authorization: Bearer <accessJwt>`.

**Refresh-токен** — **opaque** случайный 256-бит (`rt_` + 43 символа base64url; это НЕ JWT), хранится сервером как sha256-хеш, TTL 30 дней, ротация при каждом использовании. Реюз отозванного refresh → отзыв **всех** сессий пользователя (`401`, код `token_reuse_detected`).

Транспорт refresh-токена (браузер): **httpOnly + Secure + SameSite=Lax cookie `cp_refresh`** — cookie недоступна для чтения JavaScript (исключает чтение/вынос refresh-токена; активная XSS всё равно может действовать от имени пользователя в рамках сессии — компенсирующие меры в [SECURITY.md](SECURITY.md) §2). Тело ответа содержит только `accessToken`. Не-браузерные клиенты (скрипты, тесты) могут передать refresh в теле `refreshToken`. Детали и статус решения — [ADR-020](DECISIONS.md) (Accepted, подтверждён 2026-11-04).

Хранение на frontend: access — только в памяти (Zustand-стор без персистентности); refresh — cookie (см. выше); **никогда** — localStorage/sessionStorage.

CSRF (cookie-аутентифицированные операции `refresh`/`logout`): `SameSite=Lax` + **валидация `Origin`** (fallback `Referer`) против разрешённого origin — точное сравнение; несовпадение или отсутствие обоих заголовков → `403` (детали — [ADR-020](DECISIONS.md), [SECURITY.md](SECURITY.md) §2).

**Конкурентная ротация refresh:** атомарный claim — `UPDATE refresh_tokens SET revoked_at = now() WHERE token_hash = $1 AND revoked_at IS NULL` в транзакции. Победивший запрос получает новую пару; конкурентный запрос с тем же токеном (UPDATE затронул 0 строк) трактуется как **reuse** → `401 token_reuse_detected` + отзыв всех сессий. Grace period отсутствует. Клиентский контракт: single-flight refresh в пределах вкладки **и межвкладочно** (Web Locks API / fallback BroadcastChannel); при неизвестном результате (таймаут/обрыв) — один повтор; полученный `token_reuse_detected` = истёкшая сессия → переход на логин (fail-safe). Полная семантика — [SECURITY.md](SECURITY.md) §2; acceptance criteria — [EVALUATION.md](EVALUATION.md) §2.

Матрица авторизации эндпоинтов:

| Категория | Эндпоинты | Требуется |
|---|---|---|
| Public | `POST /auth/register`*, `POST /auth/login`, `POST /auth/refresh` (refresh-токен), `GET /health`, `GET /ready` | ничего / refresh-токен для refresh |
| Access JWT | все остальные, включая `PUT /auth/password`; `POST /auth/logout` — access JWT **либо** валидная refresh-cookie с пройденной Origin-проверкой | `Authorization: Bearer` |

\* `POST /auth/register` публичен **только в SaaS-фазе**; в personal MVP регистрация закрыта флагом `CP_ALLOW_REGISTRATION=false` → `403` `registration_disabled` (см. [SECURITY.md](SECURITY.md) §12, ADR-021).

Асинхронные операции возвращают `202 Accepted` + ресурс со статусом; клиент поллит ресурс. Примеры статусов: resumes (`parsing` → `parsed`), copilot (`running` → `waitingUser` → `completed`).

### 1.2 Идемпотентность и конкурентность

`Idempotency-Key` (UUID, задаёт клиент): сервер хранит ключ + ответ 24 ч (Redis, scope: user + endpoint); повтор с тем же ключом возвращает закешированный результат, не выполняя операцию повторно.

| Операция | Гарантия |
|---|---|
| `POST /resumes` | повтор того же файла → существующая запись (checksum-дедуп в БД); повтор с тем же `Idempotency-Key` → тот же ответ |
| `POST /applications/{id}/copilot/start` | если run уже активен (`running`/`waitingUser`) → `409` `conflict`; повтор с тем же ключом → идентичный ответ с тем же `runId` |
| `POST /applications/{id}/copilot/resume` | конкурентные resume сериализуются блокировкой строки `copilot_run` (`SELECT ... FOR UPDATE`): побеждает первый, второй → `409`; повтор того же ключа → replay без повторного выполнения. Ответ на run не в статусе `waitingUser` → `409` |
| `PATCH /facts/{id}` | оптимистичная блокировка: в теле ожидается `expectedUpdatedAt`; несовпадение → `409` `conflict` |
| `POST /job-searches/{id}/run` | повторный запуск подавляется, если run уже `running` (→ `409`) |

Правила retry для клиентов: `5xx`/таймаут — безопасный retry (с тем же `Idempotency-Key`); `409` — прочитать состояние ресурса, не ретраить вслепую.

---

## 2. Список эндпоинтов

### 2.1 System

| Метод | Путь | Auth | Описание |
|---|---|---|---|
| GET | `/health` | public | liveness |
| GET | `/ready` | public | readiness: DB, Redis, S3 |

### 2.2 Auth & Users (`identity`)

| Метод | Путь | Auth | Описание |
|---|---|---|---|
| POST | `/auth/register` | public* | регистрация email+пароль (в MVP отключена, §1.1) |
| POST | `/auth/login` | public | вход → access + refresh-cookie |
| POST | `/auth/refresh` | refresh | ротация refresh (cookie или тело) |
| POST | `/auth/logout` | access или refresh-cookie (+Origin) | отзыв refresh-сессии (refresh из cookie или тела); при доступе только по refresh-cookie (истёкший access) операция ограничена отзывом этой сессии |
| PUT | `/auth/password` | access | смена пароля (старый + новый) |
| GET | `/users/me` | access | профиль пользователя |
| PATCH | `/users/me` | access | locale, timezone, displayName |
| POST | `/users/me/export` | access | запрос экспорта данных (async, §7) |
| GET | `/users/me/export/{exportId}` | access | статус/ссылка на готовый архив (presigned URL) |
| DELETE | `/users/me` | access | удаление аккаунта (подтверждение паролем) |

### 2.3 Career Profiles (`career_profile`) — все access

| Метод | Путь | Описание |
|---|---|---|
| GET / POST | `/career-profiles` | список / создание |
| GET / PATCH / DELETE | `/career-profiles/{id}` | чтение / изменение / удаление |
| POST | `/career-profiles/{id}/activate` | сделать активным |

### 2.4 Resumes & Facts (`resume_intelligence`) — все access

| Метод | Путь | Описание |
|---|---|---|
| POST | `/resumes` | загрузка файла (multipart), запуск парсинга (202) |
| GET | `/resumes` | список со статусами |
| GET / DELETE | `/resumes/{id}` | статус/метаданные / удаление |
| GET | `/resumes/{id}/file` | выдача файла (presigned URL) |
| POST | `/resumes/{id}/reparse` | повторный парсинг |
| GET | `/career-profiles/{id}/facts` | факты профиля (`?type=&status=`) |
| POST | `/career-profiles/{id}/facts` | добавить факт вручную |
| PATCH | `/facts/{factId}` | действие над фактом: `action: confirm \| edit \| reject` (+ `expectedUpdatedAt`) |

Дублирующий `POST /facts/{id}/confirm` удалён (аудит v1.1): подтверждение — частный случай `PATCH` с `action=confirm`.

### 2.5 Job Searches & Sources (`job_discovery`, `job_sourcing`) — все access

| Метод | Путь | Описание |
|---|---|---|
| GET / POST | `/job-searches` | список / создание подписки |
| GET / PATCH / DELETE | `/job-searches/{id}` | управление |
| POST | `/job-searches/{id}/run` | ручной запуск (202; 409 если уже running) |
| GET | `/job-searches/{id}/runs` | история прогонов |
| GET | `/sources` | реестр источников + health (read-only) |

### 2.6 Jobs & Matches (`job_dedup`, `job_matching`) — все access

| Метод | Путь | Описание |
|---|---|---|
| GET | `/jobs` | лента вакансий (фильтры: статус, remote, зарплата, язык, поиск; сортировка: score/published) |
| GET | `/jobs/{id}` | карточка вакансии (canonical + postings-источники) |
| GET | `/jobs/{id}/versions` | история изменений |
| POST | `/jobs/{id}/recheck` | ручная верификация свежести (202) |
| GET | `/matches` | матчи активного профиля (`?minScore=&decision=&hardFilter=`) |
| GET | `/matches/{id}` | матч с полным объяснением |
| POST | `/jobs/{id}/match` | рассчитать матч по требованию (202) |
| PATCH | `/matches/{id}/decision` | решение: interested / dismissed / notInterested / willApply |

### 2.7 Applications & Copilot (`applications`, `copilot`) — все access

| Метод | Путь | Описание |
|---|---|---|
| GET / POST | `/applications` | список / создать черновик (jobId) |
| GET / PATCH / DELETE | `/applications/{id}` | карточка / статусы и поля / архив |
| POST | `/applications/{id}/copilot/start` | запуск workflow (202; 409 при активной run) |
| GET | `/applications/{id}/copilot/status` | состояние run: нода, статус, вопросы/ревью |
| POST | `/applications/{id}/copilot/resume` | ответ на interrupt / продолжение (§1.2 — конкурентность) |
| POST | `/applications/{id}/copilot/cancel` | отмена run |
| GET | `/applications/{id}/documents` | документы (все версии) |
| GET | `/documents/{documentId}` | версия документа |
| PATCH | `/documents/{documentId}` | пользовательская правка (новая версия) |
| POST | `/documents/{documentId}/approve` | одобрение (финализация) |
| POST | `/documents/{documentId}/regenerate` | перегенерация с фидбеком |
| GET | `/documents/{documentId}/export?format=md\|txt\|docx` | экспорт — контракт в §6.4 |
| GET | `/applications/stats` | воронка (counts, конверсии) |

### 2.8 Notifications (`notifications`) — все access

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
{ "email": "user@example.com", "password": "correct-horse-battery", "locale": "ru" }

// 201 (+ Set-Cookie: cp_refresh=...; HttpOnly; Secure; SameSite=Lax)
{ "id": "0192a5f0-8c3e-7cc2-9f31-3a2b6d8e1f01", "email": "user@example.com",
  "locale": "ru", "accessToken": "eyJ...", "tokenType": "bearer", "expiresIn": 900 }
```

### POST /api/v1/auth/login → 200 (тот же формат); POST /api/v1/auth/refresh

```json
// request (браузер — пустое тело, refresh берётся из cookie; CLI — в теле)
{ "refreshToken": "rt_3FZ9tKq2vX8mB4wY6nJ1sD5gH7kL0pR2aE4uI9oP3cxA" }

// 200 — новая пара (ротация); старый refresh отзывается
{ "accessToken": "eyJ...", "tokenType": "bearer", "expiresIn": 900 }
// + Set-Cookie: cp_refresh=<новый opaque>; HttpOnly; Secure; SameSite=Lax
```

```json
// 401 — реюз отозванного refresh (все сессии пользователя отозваны)
{ "error": { "code": "token_reuse_detected", "message": "Refresh token reuse detected; all sessions revoked",
             "details": {}, "requestId": "0192aa02-..." } }
```

## 4. Примеры: резюме и факты

### POST /api/v1/resumes (multipart/form-data, Idempotency-Key: <uuid>)

```
resume=@cv_ai_engineer.pdf
```

```json
// 202
{ "id": "0192a7c1-5d20-7b1a-8e4c-1f2a3b4c5d61", "status": "parsing",
  "filename": "cv_ai_engineer.pdf", "sizeBytes": 241331, "createdAt": "2026-11-04T12:00:00Z" }
```

### GET /api/v1/resumes/{id} — после завершения

```json
{ "id": "0192a7c1-...", "status": "parsed", "language": "en", "parsedAt": "2026-11-04T12:01:40Z",
  "factsSummary": { "total": 87, "draft": 87, "confirmed": 0,
                    "byType": { "skill": 34, "experience": 6, "project": 9 } } }
```

### GET /api/v1/career-profiles/{id}/facts?type=experience&status=draft

```json
{ "items": [
  { "id": "0192a8e2-...", "factType": "experience", "status": "draft", "confidence": 0.93,
    "source": "llm_extraction", "updatedAt": "2026-11-04T12:02:00Z",
    "payload": { "company": "OOO Romashka", "title": "Senior Frontend Developer",
                 "start": "2022-03", "end": null, "employment": "full_time" },
    "evidence": [ { "resumeId": "0192a7c1-...", "section": "experience",
                    "quote": "Senior Frontend Developer @ Romashka (Mar 2022 — present) ..." } ],
    "verified": false }
],
"nextCursor": null }
```

### PATCH /api/v1/facts/{factId} — подтверждение с правкой

```json
// request
{ "action": "confirm", "expectedUpdatedAt": "2026-11-04T12:02:00Z",
  "payloadEdits": { "end": "2025-11" } }
// 200
{ "id": "0192a8e2-...", "status": "confirmed", "verified": true, "verifiedAt": "2026-11-04T12:10:00Z",
  "payload": { "company": "OOO Romashka", "title": "Senior Frontend Developer",
               "start": "2022-03", "end": "2025-11", "employment": "full_time" } }
```

```json
// 409 — факт изменился между чтением и записью (оптимистичная блокировка)
{ "error": { "code": "conflict", "message": "Fact was modified by another request",
             "details": { "currentUpdatedAt": "2026-11-04T12:05:11Z" }, "requestId": "..." } }
```

## 5. Примеры: вакансии и матчи

### GET /api/v1/matches?minScore=0.65&hardFilter=passed&limit=2

```json
{ "items": [
  { "id": "0192b1a0-...", "job": { "id": "0192b0ff-...", "title": "LLM Application Engineer",
      "companyName": "Acme AI", "remoteType": "remote",
      "salary": { "min": 350000, "max": 500000, "currency": "RUB" },
      "publishedAt": "2026-11-01T09:00:00Z", "bestSourceUrl": "https://..." },
    "finalScore": 0.81, "hardFilterPassed": true,
    "components": { "skills": 0.82, "experience": 0.9, "semantics": 0.76, "logistics": 1.0 },
    "skillGaps": [ { "requirement": "Kubernetes в проде", "critical": true } ],
    "decision": "none", "computedAt": "2026-11-04T13:00:00Z" }
],
"nextCursor": "Mg" }
```

### GET /api/v1/matches/{id} — фрагмент объяснения

```json
{ "id": "0192b1a0-...", "finalScore": 0.81, "hardFilterPassed": true,
  "explanation": {
    "summary": "Сильный матч: 8/10 обязательных требований закрыты",
    "evidence": [
      { "requirement": "Опыт production RAG", "verdict": "covered",
        "factIds": ["0192a8e5-..."], "evidenceQuote": "built hybrid retrieval (BM25+pgvector), deflection +35%" },
      { "requirement": "Kubernetes", "verdict": "gap", "critical": true,
        "hint": "в профиле нет подтверждённого факта; есть Docker (fact 0192a8f0-...)" }
    ] },
  "job": { "...": "полная карточка" } }
```

## 6. Примеры: Application Copilot

### POST /api/v1/applications

```json
// request
{ "jobId": "0192b0ff-...", "source": "copilot" }
// 201
{ "id": "0192c000-...", "jobId": "0192b0ff-...", "status": "draft", "createdAt": "..." }
```

### POST /api/v1/applications/{id}/copilot/start (Idempotency-Key: <uuid>) → 202

```json
{ "runId": "0192c001-...", "status": "running", "currentNode": "job_analyzer" }
```

### GET /api/v1/applications/{id}/copilot/status — граф дошёл до request_user_input

```json
{ "runId": "0192c001-...", "status": "waitingUser", "currentNode": "request_user_input",
  "await": {
    "kind": "questions",
    "questions": [
      { "id": "q1", "question": "Есть ли у вас опыт выравнивания LLM (LoRA/DPO) на своих проектах?",
        "why": "Вакансия упоминает fine-tuning как обязательное требование",
        "relatedGap": "ML fine-tuning", "optional": true },
      { "id": "q2", "question": "Какой самый заметный измеримый эффект дал ваш RAG-проект?",
        "why": "Усилит резюме и cover letter", "relatedGap": null, "optional": true }
    ]
  },
  "progress": { "nodesDone": ["job_analyzer", "profile_matcher", "gap_analyzer"] } }
```

### POST /api/v1/applications/{id}/copilot/resume — ответ пользователя

```json
// request (Idempotency-Key: <uuid>)
{ "answers": [ { "id": "q1", "answer": "Да, LoRA на Llama-3 для классификации тикетов, +12% accuracy",
                 "confirmFact": true },
               { "id": "q2", "answer": "deflection 35% за квартал", "confirmFact": true } ] }
// 202
{ "runId": "0192c001-...", "status": "running", "currentNode": "resume_generator" }
```

`confirmFact: true` — ответ сохраняется как новый `profile_fact` (source=user_input, verified), переиспользуется в дальнейших откликах.

### GET .../copilot/status — граф дошёл до human_review

```json
{ "status": "waitingUser", "currentNode": "human_review",
  "await": {
    "kind": "review",
    "documents": [
      { "documentId": "0192d001-...", "kind": "resume", "version": 1, "reviewStatus": "draft",
        "factCheck": { "supported": 41, "unsupported": 0, "unverifiable": 2,
          "flags": [ { "claim": "увеличил конверсию откликов", "verdict": "unverifiable",
                       "note": "нет измеримого факта в профиле" } ] } },
      { "documentId": "0192d002-...", "kind": "cover_letter", "version": 1 },
      { "documentId": "0192d003-...", "kind": "answers", "version": 1 }
    ] },
  "progress": { "nodesDone": ["...генераторы", "fact_checker"] } }
```

### POST /api/v1/documents/{documentId}/approve → 200; итог workflow

```json
{ "applicationId": "0192c000-...", "status": "completed",
  "applicationStatus": "materials_ready",
  "documents": [ { "kind": "resume", "version": 1, "reviewStatus": "approved" },
                 { "kind": "cover_letter", "version": 2, "reviewStatus": "approved" } ] }
```

### 6.4 Контракт экспорта документов

Форматы: `md`, `txt` — синхронно; `docx` — асинхронный рендер; `pdf` — отложено (Stage 6+; ранее заявлен в PRD — согласовано Stage 0.1).

```
GET /api/v1/documents/{id}/export?format=md|txt   → 200, тело файла (content-type: text/markdown | text/plain)
GET /api/v1/documents/{id}/export?format=docx     → 202 { "exportId": "...", "status": "rendering" }
GET /api/v1/documents/{id}/export/{exportId}      → 200 { "status": "ready", "downloadUrl": "<presigned, TTL 15 мин>" }
```

`downloadUrl` — presigned URL на S3-объект `users/{uid}/applications/{aid}/{kind}-v{n}.docx`; доступ только владельцу (проверка до выдачи URL).

## 7. Примеры: остальные ресурсы

### POST /api/v1/job-searches

```json
// request
{ "name": "AI/LLM Engineer, remote",
  "query": { "keywords": ["AI Engineer", "LLM Engineer", "AI Application Engineer"],
             "sources": ["arbeitnow", "remotive", "adzuna", "hn_algolia"],
             "remoteOnly": true },
  "schedule": { "intervalMinutes": 120 } }
// 201
{ "id": "0192e000-...", "isActive": true, "nextRunAt": "2026-11-04T14:00:00Z", "...": "..." }
```

### POST /api/v1/notifications/telegram/link-code

```json
// 201
{ "code": "CP-8F3K2QA7", "expiresIn": 600,
  "botUsername": "careerpilot_bot", "instructions": "Отправьте код боту в Telegram" }
```

Код: 8 символов (Crockford base32, ~40 бит энтропии), одноразовый, хранится как sha256 (Redis, TTL 10 мин), не более 5 попыток ввода и 5 кодов/час на пользователя — детали [SECURITY.md](SECURITY.md) §2.

### POST /api/v1/users/me/export → 202

```json
{ "exportId": "0192f000-...", "status": "processing",
  "note": "Статус и ссылка — GET /users/me/export/{exportId} (готовый архив; presigned URL TTL ≤ 15 мин, новую ссылку можно перезапросить; артефакт доступен 24 ч)" }
```

### 7.4 Application State Machine

Переходы статусов (исполнитель: `copilot` — автоматические, `user` — ручные через API):

| Из | В | Условие / исполнитель |
|---|---|---|
| draft | awaiting_input | copilot: start workflow дошёл до interrupt |
| draft | materials_ready | copilot: workflow без вопросов дошёл до одобрения |
| draft | submitted | user: отклик отправлен вручную без копайлота |
| awaiting_input | materials_ready | copilot: ответы получены, документы одобрены в human_review |
| awaiting_input | draft | user: cancel/отклонение вопросов |
| materials_ready | submitted | user: отправил отклик самостоятельно (appliedAt) |
| submitted | interview | user |
| submitted / interview | offer | user |
| submitted / interview / offer | rejected \| withdrawn | user |
| любой не-terminal | archived | user |

Terminal: `rejected`, `withdrawn`, `archived` (редактирование карточки остаётся). Любой иной переход → `422` `domain_rule_violation`. История переходов — `application_status_history`.

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
             "details": { "resource": "resume" }, "requestId": "0192aa01-..." } }
// 422 (бизнес-правило)
{ "error": { "code": "domain_rule_violation",
             "message": "Cannot transition application from 'offer' to 'submitted'",
             "details": { "from": "offer", "to": "submitted" }, "requestId": "..." } }
// 429
{ "error": { "code": "rate_limited", "message": "Too many copilot runs",
             "details": { "retryAfterSeconds": 3600 }, "requestId": "..." } }
```

Каталог кодов: `validation_error`, `unauthorized`, `token_reuse_detected`, `forbidden`, `registration_disabled`, `not_found`, `conflict`, `domain_rule_violation`, `rate_limited`, `external_service_error`, `internal_error`. Сообщения локализуются по `Accept-Language` (ru/en), `code` стабилен для фронтенда.

## 9. Версионирование и совместимость

- Несовместимые изменения — новый префикс `/api/v2`; в пределах v1 — только additive-изменения (новые поля/эндпоинты).
- Deprecation: заголовок `Deprecation: true` + `Sunset` (RFC 8594), минимум 2 месяца поддержки.
- Webhooks (Telegram → API) не входят в публичный контракт: внутренний механизм, формат зависит от Bot API.

## 10. Замечания для Stage 1

- OpenAPI-схема фронтенд получает через codegen (`openapi-typescript`) — ручных типов на фронтенде быть не должно.
- Все ответы списков — единая обёртка `{items, nextCursor}`.
- CORS: только origin Dashboard; для personal-деплоя допустим same-origin (nginx проксирует `/api`).
- Cookie `cp_refresh`: path=`/api/v1/auth`, чтобы не отправляться на каждый запрос.
- Rate limiting API: per-user (Redis, token bucket) — базовые пределы Stage 8, copilot-лимиты раньше (стоимость LLM).
