# CareerPilot AI — Source Integration Architecture

- **Статус:** Draft v1.0 (Stage 0 — проектирование; ни один адаптер не реализован).
- **Связанные:** [ARCHITECTURE.md](ARCHITECTURE.md) · [DATABASE.md](DATABASE.md) · [PRD.md](PRD.md)

---

## 1. Принципы

1. **Только разрешённые способы.** Официальные API, RSS/ATOM-фиды, публичные ATS-эндпоинты. Без scraping за логином, без обхода CAPTCHA/rate-limit-защит, без подмены User-Agent с целью обойти блокировки, без нарушения ToS площадок.
2. **Каждый источник — адаптер за контрактом.** Новый источник не меняет ядро: реализуй интерфейс, зарегистрируй в `job_sources`.
3. **Источник недоверен.** Всё, что вернул источник, — недоверенные данные (`raw_data`), нормализация валидирует и очищает.
4. **Уважение к ресурсам источников.** Rate limits (свои, консервативнее чужих), backoff по `Retry-After`, кэширование, низкая частота опроса личного использования.
5. **Изоляция сбоев.** Падение/блокировка одного источника не влияет на остальные.

## 2. Статус источников (проверено 2026-11-03)

> Формат: статус = `подтверждено` (проверено по официальным источникам на дату Stage 0) или `требует проверки` (не подтверждено; подключать только после проверки). Отсутствие в таблице = не рассматривалось.

| Источник | Тип | Статус | Факт / что делать |
|---|---|---|---|
| **hh.ru** | API | `подтверждено, доступ ограничен` | Публичный API существует (api.hh.ru, OpenAPI-документация), но: с декабря 2025 закрыт публичный API для соискателей, с апреля 2026 `GET /vacancies` возвращает 403 неавторизованным. Легальный доступ — платная услуга «Доступ к API hh» (договор; публичные упоминания тарифов порядка 19 900–49 900 ₽/мес — уточнять у hh.ru, api@hh.ru). **Решение владельца**: купить доступ или отложить RU-агрегатор |
| **Arbeitnow** | API | `подтверждено` | Бесплатный публичный job-board API (документирован), есть поле remote; без ключа. Проверить условия при интеграции |
| **Remotive** | API | `подтверждено (первично)` | Публичный feed `api.remotive.com` для remote-вакансий, без ключа. Проверить актуальный формат и условия |
| **Adzuna** | API | `подтверждено` | Официальный developer API (ключ по регистрации), агрегатор, есть free tier. Для наших ролей и локаций |
| **HN «Who is hiring»** | API | `подтверждено` | Официальный Algolia HN Search API (`hn.algolia.com`) — документированный способ получать треды/комментарии. AI-стартапы активно постят там. Адаптер: поиск Ask HN тредов месяца + парсинг комментариев (неструктурированные — сильная нагрузка на normalizer) |
| **Greenhouse boards** | ATS | `требует проверки` | Публичные JSON-эндпоинты job-бордов компаний широко документированы; перед подключением — проверить ToS конкретных компаний и частоту опроса. Список целевых компаний задаёт пользователь |
| **Lever postings** | ATS | `требует проверки` | Аналогично: публичный JSON postings per-company; проверить условия |
| **Ashby / Workable** | ATS | `требует проверки` | Публичные API карьерных страниц ряда компаний; проверить |
| **Карьерные страницы (RSS)** | RSS | `требует проверки` | Только если страница официально отдаёт RSS/Atom или публично документированный JSON; пер-компаний |
| **LinkedIn** | — | `публичного API нет (для нашего сценария)` | Официального API поиска вакансий для личного использования нет; партнёрские программы не для нас. **Scraping запрещён решением Stage 0** |
| **Indeed** | — | `требует проверки` | Партнёрский/publisher API существует, но доступ ограничен и не подтверждён для нашего сценария. Не подключать до подтверждения |
| **Wellfound** | — | `публичного API нет` | Не подключать; пересматривать при появлении официального API |
| **Работа.ру** | — | `требует проверки` | Публично документированного API не найдено на Stage 0; связаться с площадкой/искать партнёрство. Не scraping |

Вывод для MVP: **первая волна интеграций** — Arbeitnow, Remotive, Adzuna, HN Algolia (все с официальным документированным доступом), затем ATS-борды целевых компаний после проверки ToS. hh.ru — отдельное продуктовое решение о платном доступе. Это отличается от исходного предположения «hh.ru первым через открытый API» — оно не подтвердилось (см. [DECISIONS.md, ADR-014](DECISIONS.md)).

## 3. Source Adapter contract

### 3.1 Интерфейс (проект, Python)

```python
class SourceAdapter(Protocol):
    code: str                    # "remotive"
    kind: SourceKind             # api | rss | ats_board | career_page
    RateLimitPolicy: ClassVar[RateLimitPolicy]

    async def discover(self, query: SourceQuery) -> AsyncIterator[RawJobPosting]:
        """Постраничная выдача по поисковому запросу. Пагинация — обязанность адаптера.
        Адаптер соблюдает RateLimitPolicy и прерывается по лимитам/violations,
        бросая SourceRateLimited / PermanentSourceError."""

    async def verify(self, posting: PostingRef) -> VerificationResult:
        """Проверка актуальности одного объявления: {status: active|closed|gone|unknown,
        changed_hint: bool, checked_at}. None для источников без точечной проверки —
        тогда verification деградирует до повторного discover."""

    def normalize(self, raw: RawJobPosting) -> NormalizedJobDraft:
        """Чистая функция raw→draft. Никакой сети. Вызывается normalizer'ом."""

    def healthcheck(self) -> SourceHealthResult: ...
```

Поддержка адаптера = реализация интерфейса + запись в `job_sources` (code, config, rate_limit) + unit-тесты на фикстурах сырых ответов + запись в реестр фабрик. **Ни один адаптер не пишет в БД напрямую** — только возвращает данные; persist делает `job_normalization`.

### 3.2 DTO «сырого» объявления (выход адаптера)

```python
@dataclass
class RawJobPosting:
    source_code: str
    external_id: str                 # стабильно уникальный id в источнике
    source_url: str                  # канонический URL
    title: str | None                # как есть; None допустим (normalizer отбросит)
    company_name: str | None
    location_text: str | None
    remote_hint: bool | None
    employment_type: str | None
    salary_text: str | None          # сырая строка/структура источника
    description_html_or_text: str    # основной контент (недоверенный)
    published_at: datetime | None    # по данным источника
    fetched_at: datetime
    raw_payload: dict                # оригинальный ответ (provenance, raw_data)
    provenance: dict                 # {endpoint, params, headers_redacted, request_id}
```

### 3.3 Ошибки адаптера (маппинг)

| Исключение | Когда | Действие ядра |
|---|---|---|
| `SourceRateLimited(retry_after)` | 429/лимиты | Пауза источника до retry_after; батч продолжит другие источники |
| `TransientSourceError` | 5xx, таймауты, обрывы | Retry c backoff+jitter (≤5 попыток), затем run=partial |
| `PermanentSourceError` | 401/403/404 схемы, нарушение формата | Без ретраев; инкремент счётчика ошибок → health=degraded (3 подряд → down) + алерт |
| `SourceAuthError` | токен источника истёк | degraded + уведомление владельцу |

Любая ошибка логируется со `source_code`, `endpoint`, обезличенными параметрами (без секретов); в `source_sync_runs.errors` — per-posting детали.

## 4. Ingestion pipeline (оркестратор — `job_discovery`)

```
job_searches (due) ──celery──▶ run_search(search_id)
  for source in search.query.sources:            # изоляция per-source
    sync_run = start(source)
    async for raw in adapter.discover(query):     # rate-limited
        draft = adapter.normalize(raw)            # чистая функция
        posting = normalizer.normalize_and_store(draft)   # upsert по (source_id, external_id)
    dedup.upsert_canonical(changed postings)      # fingerprint → jobs/job_versions
  matching.match_pending(profile_id)              # по событию job.changed
  finish run(stats)
```

Детали:

- **Пагинация** — внутри `discover` (адаптер знает формат: cursor/page/scroll); глобальный кап на страницу запуска (гипотеза: 500 postings/run/source) — защита от «бесконечных» лент.
- **Идемпотентность:** повторный прогон того же search безопасен — upsert по `(source_id, external_id)`; `normalized_checksum` не изменился → skip записи версии.
- **Rate limit:** token bucket в Redis (`cp:sourcing:{code}:rl`), консервативнее лимитов источника; глобальный daily cap per source; honoring `Retry-After`.
- **Дедупликация запросов:** ETag/If-Modified-Since, где источник поддерживает.

## 5. Дедупликация (job_dedup)

1. **Exact:** `fingerprint = sha256(title_norm ‖ company_norm ‖ location_norm)`; нормализация: NFKC, lowercase, удаление legal-суффиксов (OOO/Inc/Ltd/ГmbH...), whitespace. Совпал → posting привязывается к существующему job.
2. **Изменения:** `normalized_checksum` posting изменился → пересборка canonical (правила merge: приоритет более свежей active posting; конфликты полей — брать из posting с более поздним `last_seen_at`) → diff не пуст → новая `job_versions` запись.
3. **Similar (Stage 6, флаг `CP_FEATURE_DEDUP_SIMILARITY`):** кандидаты по pg_trgm(company_norm) + cosine(embedding) ≥ порог; слияние только через подтверждение владельцем (ложные слияния необратимы без ручной чистки).

## 6. Верификация актуальности

- Планировщик (beat): активные postings, у которых `last_verified_at` старше гипотезы-интервала (24 ч для вакансий в активных поисках, 7 дней для остальных), попадают в очередь recheck батчами (кап запросов к источнику).
- `adapter.verify(posting)` → статус: `active` (обновить last_verified_at) / `closed` (источник явно сказал) / `gone` (404/удалена) / `unknown`.
- `jobs.status`: все postings closed/gone → `closed`; не верифицировалась > 7 дней → `stale` (в выдаче с пометкой).
- Ручной `POST /jobs/{id}/recheck` — форс-проверка.

## 7. Правила работы с источниками (compliance checklist адаптера)

- [ ] Найден официальный документированный способ доступа (API docs / RSS / публичный борд).
- [ ] ToS/условия источника прочитаны; нет запрета на наш сценарий (периодический сбор для личного использования).
- [ ] Указан и уважается rate limit (наш — консервативнее); honor `Retry-After`.
- [ ] Нет обхода авторизации/CAPTCHA; не используются endpoint'ы, требующие входа, без официального токена.
- [ ] robots.txt проверен для прямых HTTP-запросов к страницам (для API не применяется, но уважается при сомнении).
- [ ] Секреты источника хранятся зашифрованно (`job_sources.credentials_enc`).
- [ ] `raw_data` хранится как provenance; доступ к нему — только внутренние нужды (ретраи нормализации, дебаг).

## 8. Пример: каркас адаптера (псевдокод, не реализация)

```python
class RemotiveAdapter:
    code = "remotive"
    kind = SourceKind.api
    RateLimitPolicy = RateLimitPolicy(requests_per_minute=10, burst=5, daily_cap=2_000)

    BASE = "https://api.remotive.com/api/v1"

    async def discover(self, query: SourceQuery):
        params = {"search": query.keywords[0], "limit": 50}
        async for page in self._paginate(f"{self.BASE}/remote-jobs", params):
            for item in page["jobs"]:
                yield self.to_raw(item)

    def to_raw(self, item: dict) -> RawJobPosting:
        return RawJobPosting(
            source_code=self.code,
            external_id=str(item["id"]),
            source_url=item["url"],
            title=item.get("title"),
            company_name=(item.get("company") or {}).get("name"),
            location_text=item.get("candidate_required_location"),
            remote_hint=True,                      # Remotive = remote board
            description_html_or_text=item.get("description", ""),
            published_at=_parse_dt(item.get("publication_date")),
            fetched_at=utcnow(),
            raw_payload=item,
            provenance={"endpoint": "/remote-jobs", "params": {...}},
        )

    def normalize(self, raw: RawJobPosting) -> NormalizedJobDraft: ...
```

Реальная реализация — Stage 3 (см. [ROADMAP.md](ROADMAP.md)); каркас показывает ожидаемую форму: адаптер тонкий, вся логика хранения/дедупа — в ядре.

## 9. Open questions по интеграциям

| # | Вопрос |
|---|---|
| I-1 | Покупать ли «Доступ к API hh» (влияет на RU-покрытие) — решение владельца |
| I-2 | Список целевых компаний для ATS-бордов (Greenhouse/Lever/Ashby) — задаёт пользователь; нужны критерии отбора |
| I-3 | Минимально достаточная частота опроса источников (гипотеза: раз в 2–4 ч) — калибровать по частоте новых вакансий |
| I-4 | Политика хранения raw_data закрытых вакансий (срок) — подтвердить при настройке retention-задач |
