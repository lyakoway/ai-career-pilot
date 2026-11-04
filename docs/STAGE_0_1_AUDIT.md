# Stage 0.1 — Security, API & Architecture Consistency Audit

- **Дата:** 2026-11-04.
- **Объект:** документация Stage 0 (docs/PRD, ARCHITECTURE, DATABASE, API_SPEC, AI_DESIGN, INTEGRATIONS, EVALUATION, SECURITY, ROADMAP, DECISIONS).
- **Результат:** найдено и устранено 4 противоречия P0, 21 проблема P1, 3 замечания P2; код приложения не менялся; изменения решений проведены через поправки/новые ADR (не молча).
- Файл отчёта лежит в `docs/` вместе с остальной документацией проекта (единое место документации; корень не засоряем).

---

## 1. Найденные противоречия и проблемы

Классификация: **P0** — введёт в заблуждение при реализации / фактическая ошибка; **P1** — пробел или несоответствие между документами; **P2** — качество, терминология, мелочи.

### P0

| # | Проблема | Где было | Исправление |
|---|---|---|---|
| P0-1 | На диаграмме PostgreSQL подписан как «**Langfuse** checkpoint tables» — чекпоинты хранит **LangGraph** (схема `langgraph`), Langfuse — внешняя система со своими хранилищами | ARCHITECTURE §3 (диаграмма) | Подпись исправлена; добавлен раздел «Хранилища и источник истины» (ARCHITECTURE §3.1) с явным разделением LangGraph ↔ Langfuse и постулатом «PostgreSQL — единственный источник истины бизнес-состояния»; AI_DESIGN §6.6 расширен (хранение/удаление чекпоинтов, поведение при потере) |
| P0-2 | Примеры refresh-токена выглядели как JWT (`"refresh_token": "eyJ..."`) — противоречит модели opaque 256-bit из SECURITY §2 / DATABASE §3.1 | API_SPEC §3 | Формат зафиксирован (`rt_<43 base64url>`), все примеры переписаны; добавлен пример `401 token_reuse_detected` |
| P0-3 | Бланкетное «все эндпоинты кроме `/auth/*` публичны» ошибочно для logout/password и не учитывало закрытую регистрацию в MVP | API_SPEC §1 | Введена матрица авторизации (public / refresh / access); `POST /auth/register` в MVP → `403 registration_disabled` (флаг `CP_ALLOW_REGISTRATION`, ADR-021, SECURITY §12) |
| P0-4 | Заявлен camelCase для JSON API, но **все** примеры были snake_case — невыполнимый контракт для фронтенда | API_SPEC §3–§7 | Все JSON-примеры переведены в camelCase (`accessToken`, `sizeBytes`, `finalScore`, `waitingUser`, `factCheck`, …); зафиксировано: snake_case остаётся во внутренних структурах (БД, graph state) |

### P1

| # | Проблема | Где было | Исправление |
|---|---|---|---|
| P1-1 | Идемпотентность/конкурентность не определены для resume upload, copilot start/resume | API_SPEC | Новый §1.2: `Idempotency-Key` (24 ч, scope user+endpoint), 409-семантика, `SELECT … FOR UPDATE` на copilot_run, replay повторов, правила retry для клиентов |
| P1-2 | Application State Machine — только ASCII-картинка, без условий/исполнителей | API_SPEC §7, DATABASE §3.10 | Таблица переходов с исполнителем (copilot/user) и условиями; терминальные статусы; `422 domain_rule_violation` на недопустимые переходы |
| P1-3 | Контракт экспорта документов неоднозначен (302 на presigned для всех форматов; PDF заявлен в PRD как S) | API_SPEC, PRD FR-AC-6 | §6.4: `md`/`txt` — синхронно (200, тело), `docx` — асинхронно (202 → exportId → presigned 15 мин), `pdf` — Stage 6+; PRD синхронизирован |
| P1-4 | Дублирование подтверждения фактов: `PATCH /facts/{id}` (action=confirm) и `POST /facts/{id}/confirm` | API_SPEC §2.4 | Дублирующий POST удалён; подтверждение — `PATCH action=confirm` |
| P1-5 | Конкурентные правки фактов не определены | API_SPEC, DATABASE §3.3 | Оптимистичная блокировка: `expectedUpdatedAt` → `409` с `currentUpdatedAt` |
| P1-6 | UUIDv7 «генерируется приложением» — но в Python 3.12 stdlib `uuid7` нет (появился в 3.14) | DATABASE §1, ADR-016 | ADR-016 поправлен: библиотека `uuid6`/собственная функция; переход на stdlib после апгрейда Python без изменения схемы |
| P1-7 | Имена доменных событий не унифицированы; диаграмма модулей использовала другие имена (JobChanged, MaterialsReady, NewStrongMatches); событие уведомлений о матчах отсутствовало в каталоге | ARCHITECTURE §4.3, §6.2 | Конвенция `<aggregate>.<verb_past>`; каталог дополнен `matches.computed` и разделением `job.upserted`/`job.changed`; подписи на диаграмме синхронизированы |
| P1-8 | At-least-once доставка без формального inbox/dedup | ARCHITECTURE §6.2 | Формализовано: естественные уникальные ключи + общий инбокс `event_deliveries (subscriber, event_id)` (DATABASE §3.14, поправка ADR-015) |
| P1-9 | Дефолт трассировки Langfuse был «policy-managed» — фактически неопределён; полные тексты CV/промптов могли попадать в трейсы | AI_DESIGN §9.3, ADR-011 | Дефолт `MaskingPolicy.STRICT`: полные тексты CV, документов, промптов и ответов **не отправляются**; `DEBUG_CONTENT` — явный opt-in; поправка ADR-011 |
| P1-10 | Celery Beat и telegram-poller были «встроены» в контейнеры воркеров — beat нельзя масштабировать, poller — отдельный цикл жизни | ARCHITECTURE §11, ROADMAP Stage 1 | Выделены отдельные сервисы `beat` (singleton) и `telegram-poller`; ROADMAP синхронизирован |
| P1-11 | Хранение токенов на frontend не описано | SECURITY §2 | Access — только память; refresh — httpOnly cookie `cp_refresh` (path=/api/v1/auth); localStorage запрещён; **новый ADR-020 (Proposed)** |
| P1-12 | Telegram-линкинг: не определены энтропия, хранение, одноразовость, rate limits | SECURITY §2, API_SPEC §7 | 8 символов Crockford base32 (~40 бит), хранение sha256 в Redis, TTL 10 мин, ≤3 активных кодов, ≤5 попыток, ≤5 генераций/час; аудит-событие |
| P1-13 | «pgcrypto envelope encryption» — неверная терминология (envelope = data keys) | SECURITY §4, DATABASE §3.4 | Application-level AES-256-GCM, формат `{key_id, nonce, ciphertext}`, ротация ключей; честное название |
| P1-14 | Отсутствовала модель audit events | SECURITY, DATABASE | Новый раздел SECURITY §11 + таблица `audit_events` (DATABASE §3.15): состав событий, append-only, retention 12 мес., анонимизация вместо удаления |
| P1-15 | Удаление пользовательских данных описано не для всех хранилищ | DATABASE §6, SECURITY §8 | Единая матрица удаления: БД-каскад → S3 → langgraph-чекпоинты → Redis (TTL) → audit_events (анонимизация) → Langfuse (best-effort); жизненный цикл экспортов (`data_exports`, TTL 24/48 ч) |
| P1-16 | File upload security: MIME от клиента, ZIP-bomb, лимиты парсера, сетевая изоляция парсера не описаны | SECURITY §6 | Новый §6.1: magic bytes, лимит распаковки (100 МБ / 10k записей), hard time-limit 60 с, изоляция в воркере, запрет сетевых запросов по URL из файла (+интеграционный тест), антивирус — осознанный остаточный риск |
| P1-17 | Безопасный рендеринг недоверенного контента не описан | SECURITY, AI_DESIGN | SECURITY §6.2: только санитизированный Markdown (DOMPurify), сырой HTML никогда; AI_DESIGN §8.8; e2e-кейс в EVALUATION |
| P1-18 | Недоверенный контент трактовался только как вакансии; явного запрета tool execution не было | AI_DESIGN §8 | §8 переписан: недоверенные = вакансии **и загружаемые документы**; п.4 — явный запрет tool calling/исполнения кода/перехода по URL в MVP |
| P1-19 | Валидация structured outputs и проверка claims по фактам описаны частично | AI_DESIGN §2, §8 | П.3 §8: Pydantic-схема → retry → ошибка; `fact_ids` проверяются кодом (существование + принадлежность + `confirmed`), битые ссылки отбрасываются с зависимыми вердиктами |
| P1-20 | MVP и SaaS-функции не разделены формально (риск преждевременных фич) | SECURITY, PRD | Новый SECURITY §12 + ADR-021: Personal MVP / SaaS-ready / SaaS; регистрация закрыта флагом; биллинг pass-through; RLS по флагу; PRD FR-IDN-1 синхронизирован |
| P1-21 | Экспорт данных пользователя: не было модели статуса/жизненного цикла | API_SPEC, DATABASE | Таблица `data_exports` + контракт `POST/GET /users/me/export[/{exportId}]`; presigned 24 ч, S3-очистка 48 ч |

### P2

| # | Замечание | Исправление |
|---|---|---|
| P2-1 | Опечатки/артефакты («транозакции», «Микросservices» в ADR-таблице, латинская «a» в «заменa») | Исправлены в ARCHITECTURE/PRD/AI_DESIGN |
| P2-2 | Парольная политика без верхней границы длины (DoS Argon2) и без ссылки на NIST | SECURITY §2: длина 10–128, NIST SP 800-63B |
| P2-3 | Tenant-изоляция для воркеров и правила выдачи presigned URL упоминались разрозненно | SECURITY §3: п.4 (воркеры — только идентификаторы) и п.5 (presigned после проверки владения) |

**Проверено без изменений:** INTEGRATIONS.md — противоречий с обновлённой моделью не найдено (rate limits, статусы источников, compliance уже согласованы); ROADMAP — одна строка Stage 1 синхронизирована с топологией (P1-10).

---

## 2. Изменённые файлы

| Файл | Характер изменений |
|---|---|
| API_SPEC.md | Переписан (v1.1): camelCase, opaque refresh + cookie, матрица авторизации, §1.2 идемпотентность/конкурентность, FSM-таблица, контракт экспорта §6.4, удалён дубль /confirm, `registration_disabled`, `token_reuse_detected` |
| ARCHITECTURE.md | §3.1 хранилища/источник истины; диаграмма исправлена; beat/poller отдельные сервисы; §6.2 inbox + каталог событий; §4.3 подписи событий; §5 async/sync-фабрики; флаги конфигурации |
| SECURITY.md | §2 (пароли, токены, frontend-хранение, Telegram-линкинг), §3 (воркеры, presigned), §4 (терминология шифрования), §6.1 upload security, §6.2 рендеринг, §8 матрица удаления, §11 audit events, §12 MVP/SaaS-режимы, §13 (бывший §11) |
| DATABASE.md | §1 (uuid6/Py3.12, классификация таблиц), §3.14 event_deliveries, §3.15 audit_events + data_exports, §3.4 credentials_enc, §6 единая матрица удаления |
| AI_DESIGN.md | §2 MaskingPolicy.STRICT, §6.6 чекпоинты (хранение/удаление/чувствительность), §8 переписан (недоверенные загрузки, no tool execution, валидация fact_ids, рендеринг), §9.3 дефолты трассировки |
| EVALUATION.md | `prompt_injection` датасет расширен (инъекции через документы, кейсы рендеринга); e2e-кейс санитизации |
| PRD.md | FR-IDN-1 (закрытая регистрация в MVP), FR-AC-6 (форматы экспорта) |
| ROADMAP.md | Stage 1: топология compose (beat, telegram-poller) |
| DECISIONS.md | Поправки ADR-011/015/016; новые ADR-020 (Proposed), ADR-021 (Accepted); журнал |

## 3. Решения, проведённые через ADR

| ADR | Статус | Суть |
|---|---|---|
| ADR-011 (поправка) | Accepted | Дефолт STRICT-трейсов Langfuse (без полных текстов) |
| ADR-015 (поправка) | Accepted | Инбокс `event_deliveries` для at-least-once |
| ADR-016 (поправка) | Accepted | `uuid6` для Python 3.12 |
| ADR-020 (новый) | **Accepted — подтверждено владельцем 2026-11-04** (CSRF-эшелонирование: SameSite=Lax + Origin/Referer validation + ротация/reuse-детекция; logout читает refresh из cookie или тела) | Хранение токенов: httpOnly cookie refresh + access в памяти |
| ADR-021 (новый) | Accepted | Режимы Personal MVP / SaaS-ready / SaaS; закрытая регистрация флагом |

## 4. Открытые вопросы (финализированы 2026-11-04)

| # | Вопрос | Решение владельца |
|---|---|---|
| Q-A1 | ADR-020: cookie для refresh против «refresh в теле» | **Подтверждён ADR-020 (Accepted):** httpOnly cookie + access в памяти; CSRF-эшелонирование (SameSite=Lax + Origin/Referer validation + ротация/reuse-детекция); logout читает refresh из cookie или тела |
| Q-A2 | `MaskingPolicy.DEBUG_CONTENT` в personal-проде | **Разрешён** только явной настройкой деплоя `CP_LLM_DEBUG_CONTENT=true` (default `false`), WARNING при старте; секреты/credentials/контакты не передаются даже в debug (безусловный blocklist); SaaS — только staging (ADR-011) |
| Q-A3 | Состав `audit_events` | MVP-набор достаточен (решения по матчам уже в `job_matches.decision`) |
| Q-A4 | Момент включения RLS | **Остается Stage 7**; app-изоляция подкреплена contract-тестами по всем поверхностям: API, фоновые задачи (guard no-op), graph runs, документы/файлы (EVALUATION §2) |
| Q-A5 | PDF-экспорт | **Вне MVP**; пересмотр на Stage 5 после проверки достаточности DOCX |
| Q-A6 | Retention jobs (12 мес.) | **Provisional-политика**, не обязательство; пересмотр при первом наполнении каталога (DATABASE §6) |

## 5. Матрица согласованности SECURITY ↔ API ↔ DATABASE ↔ ARCHITECTURE ↔ AI_DESIGN

| Концепция | SECURITY | API_SPEC | DATABASE | ARCHITECTURE | AI_DESIGN |
|---|---|---|---|---|---|
| Access JWT 15 мин / refresh opaque 256-bit с ротацией и reuse-детекцией | §2 | §1.1 + примеры | §3.1 refresh_tokens | — | — |
| Хранение токенов на клиенте | §2 (ADR-020) | §1.1 | — | — | — |
| Авторизация эндпоинтов / закрытая регистрация MVP | §12 | §1.1, §2.2 | — | §9 (флаг) | — |
| camelCase API / snake_case внутренних структур | — | §1 (все примеры) | §1 конвенции | — | §6.3 graph state |
| Статусы copilot (`waitingUser` …) | — | §1, §6 | §3.9 | §4.1 | §6.1, §6.5 |
| Идемпотентность/конкурентность (upload, copilot, facts) | — | §1.2 | uq-индексы, §3.3/§3.9 | §6.2, §5 | §6.5 |
| Application FSM | — | §7.4 | §3.10 | — | §6 (materials_ready) |
| Экспорт данных (lifecycle) / экспорт документов (форматы) | §8 | §6.4, §7 | §3.15, §3.10 | — | — |
| События: каталог + at-least-once + inbox | — | — | §3.14 | §6.2 | — |
| Хранилища и источник истины; LangGraph ≠ Langfuse | §1 | — | §1 (схемы) | §3.1 | §6.6 |
| PII и трейсы Langfuse (STRICT-дефолт) | §1, §8 | — | — | — | §9.3 (ADR-011) |
| Недоверенный контент (вакансии + загрузки), no tool execution, валидация fact_ids | §6.1, §6.2, §7 | — | — | — | §8 |
| Tenant-изоляция (API 404, воркеры, presigned, RLS) | §3 | §1 (404-семантика) | §5 | §4 | — |
| Удаление данных (единая матрица) | §8 | §7 | §6 | — | §6.6 |
| MVP / SaaS-ready / SaaS scope | §12 (ADR-021) | §1.1 | — | §9 | — |
| Шифрование секретов источников | §4 | — | §3.4 | — | — |

Пустая ячейка = концепция в этом документе не затрагивается (и не должна).

## 6. Риски, сознательно оставленные в MVP

1. **Антивирус загрузок отсутствует** — файлы приватны, парсер без сетевого доступа, рендеринг только текста (SECURITY §6.1).
2. **Парсер в общем `ai`-воркере без контейнерной сетевой изоляции** — запрет исходящих запросов из parse-пути на уровне кода + интеграционный тест; выделенный worker-профиль — SaaS-фаза.
3. **Fail-open Langfuse** — возможна потеря трейсов; продукт не страдает (ADR-011).
4. **Вероятностная защита от LLM-инъекций** — абсолютной не существует; принятый уровень: изоляция контента + structured outputs + no-tools + обязательный human review (AI_DESIGN §8.9).
5. **Access-JWT активен ≤ 15 мин после logout** — принятый компромисс stateless-токенов.
6. **RLS выключен** в personal MVP — изоляция прикладным уровнем + contract-тесты; включение флагом на Stage 7 (Q-A4).
7. **Telegram-бот read-only** — все изменения данных только в Dashboard.
8. **`audit_events` без внешней WORM-системы** — append-only правами БД-роли приложения.
9. **История пересчётов матчей** — компактный `history` jsonb вместо полной таблицы (компромисс DATABASE §8).
10. **Нет 2FA/email-верификации** — закрытый деплой; SaaS-фаза (SECURITY §12).
11. **Удаление из бэкапов — по циклу ротации (провижн ≤ 30 дней), не мгновенно** — «немедленное удаление везде» невозможно; бэкапы шифруются, restore в прод — только с вычисткой удалённых тенантов (DATABASE §6, добавлено при финализации).
12. **Задачи в полёте на момент удаления пользователя** — нейтрализуются guard-clause (no-op без записи); теоретическое окно «воскрешения данных из очереди» закрыто tenant-осведомлёнными репозиториями и проверяется contract-тестами (EVALUATION §2).

## 7. Проверка критериев приёмки Stage 0.1

- ✅ Документы согласованы (матрица §5).
- ✅ Модель токенов/авторизации единая (opaque refresh + JWT access, матрица, reuse-детекция).
- ✅ Модель PII ↔ Langfuse единая (STRICT-дефолт, §9.3 ↔ ADR-011 ↔ SECURITY §8).
- ✅ LangGraph-чекпоинты и Langfuse-хранилища описаны раздельно (ARCHITECTURE §3.1, AI_DESIGN §6.6).
- ✅ JSON-примеры camelCase.
- ✅ Ownership всех пользовательских данных определён (tenant-таблицы, S3-ключи, чекпоинты, трейсы).
- ✅ Retry/idempotency/concurrency для Copilot определены (API_SPEC §1.2, AI_DESIGN §6.5).
- ✅ Правила удаления данных — единая матрица (DATABASE §6).
- ✅ MVP и SaaS scope разделены (SECURITY §12, ADR-021).
- ✅ Все изменения отражены в этом отчёте; код проекта не изменён.
