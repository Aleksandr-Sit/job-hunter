# Architecture

How data flows through the pipeline, and why it's built this way. For setup/usage see
[README.md](../README.md).

## Data flow

```
11 parsers (Greenhouse, Lever, HH.ru, RemoteOK, LinkedIn, CryptoJobsList,
LaborX, Remote3, 11 Telegram channels, ...)
        │  run in parallel (ThreadPoolExecutor) — src/scheduler.py:_build_parsers
        ▼
   ~1800 raw jobs/run
        │
        ▼
Dedup — SQLite seen_jobs, by job.id (storage.is_seen_batch / mark_seen_batch)
        │  unseen jobs marked seen HERE, before matching (see "Idempotent restarts" below)
        ▼
Pre-filter — src/matcher/pre_filter.py, rules in config/criteria.yaml
   1. Hard gate: instant reject (C-level/founder titles, pure dev roles,
      non-RU/EN language, no role/domain keywords at all)
   2. Weighted score 0-100: soft penalties (Director/Head/VP/Lead titles,
      fluent/native/C1/C2 English, 6+ years exp) vs boosts (remote, relocation-OK
      country, role-specific keywords)
        │  typically ~1800 → 0-5 jobs survive (rule-based, $0, runs before
        │  spending any LLM budget)
        ▼
AI Matching — src/matcher/cerebras_matcher.py
   Cerebras (Llama 3.3 70B, free tier, OpenAI-compatible SDK), batches of 5
   jobs/request, scores 0-100 against the candidate's resume/skills/preferences
        │  checkpointed per batch → data/matches.jsonl + match_cache table
        │  (safe to restart mid-run without re-paying for already-scored jobs)
        ▼
Telegram — src/bot/notifier.py
   Only score >= threshold (config/settings.yaml: matching.threshold) sent,
   sorted by score, with "Открыть" (url) and "Пропустить" (delete message) buttons
```

## Why two filtering stages before the LLM call

The pre-filter (regex/keyword rules) runs first and is free. The LLM call is the only
step that costs anything (rate-limited free tier) and the only one that can reason
about nuance ("this Director title is for a 3-person startup, not enterprise
management"). Splitting them means:

- ~1800 jobs/run never reach the LLM — the gate+score model throws out anything that's
  obviously wrong (wrong domain, wrong function, dev role, executive role) in
  microseconds, for free.
- Every rejection from the pre-filter has a logged reason (`passed_gate`, `reasons`
  list in `score_vacancy()`) — when a real job gets wrongly filtered out, it's a
  5-second grep through `config/criteria.yaml`'s keyword lists to find out why,
  instead of an opaque ML model to retrain.
- The LLM only sees jobs that already plausibly fit, so its system prompt can focus
  on nuanced scoring instead of basic eligibility — cheaper context, better signal.

## Idempotent restarts vs retry-on-failure

`mark_seen_batch()` runs in `scheduler.py:run_once()` *before* AI matching, not after.
This means a restart mid-run never re-fetches or re-pre-filters jobs already seen —
but it also means if the Cerebras call fails for a given job (timeout, malformed
response), that job is gone for good; it won't be retried on the next scheduled run.

This is a deliberate trade-off, not an oversight: the alternative (mark-seen only
after a successful AI score) would re-process the same failed jobs every cycle,
burning free-tier rate limit on jobs that may be failing for a structural reason
(e.g. consistently malformed LLM output). Given the free tier's request budget,
"skip and move on" was chosen over "retry forever." A `_AIGeoBlockError` (403, e.g. a
bad/expired API key) still short-circuits the whole run rather than burning the
remaining batches on a provider that's clearly not going to respond.

## Why polling, not a webhook, for the Telegram "Пропустить" button

`src/bot/callback_handler.py` runs `Application.run_polling()` in a daemon thread
alongside the `BlockingScheduler` main loop, instead of a webhook. A webhook needs a
public HTTPS endpoint with a valid certificate; polling works identically behind any
NAT/firewall with zero extra infrastructure — appropriate for a single-container
personal tool with no exposed ports. The cost is a background thread inside the same
process as the scheduler; both share the container's lifecycle (`restart:
unless-stopped` in docker-compose.yml), so neither can fail independently of the
other — acceptable for a 1-user bot, would need separating into two
services/processes if this were ever multi-tenant.

## Why SQLite, not Postgres/Redis

Single container, single writer, no concurrent-access requirements, and the entire
dataset (`seen_jobs`, `match_cache`) is small enough that `jobs.db` stays in the tens
of MB range. A managed database would add an external dependency and a credential to
manage for no operational benefit at this scale. `journal_mode=DELETE` (not the
default WAL) is set explicitly in `storage.get_conn()` because the `data/` directory
is bind-mounted from the host through Docker — WAL's shared-memory file
(`-shm`) doesn't always survive a host/container restart cleanly on bind mounts,
which previously produced a `disk I/O error` (see the native→Docker migration note in
README's Setup section).

## Решения и компромиссы

| Решение | Почему | Цена |
|---|---|---|
| Regex/keyword pre-filter, не ML-классификатор | Бесплатно, мгновенно, и главное — объяснимо: для каждого отказа есть причина (`reasons` в `score_vacancy()`), можно за секунды понять, почему вакансия отсеялась, и поправить список в `criteria.yaml` | Требует ручной поддержки списков ключевых слов; не обобщается на формулировки, которых там нет |
| Cerebras (free tier) вместо платного провайдера | Бюджет проекта — 0₽; OpenAI-совместимый SDK даёт лёгкую миграцию между провайдерами (этот проект уже пережил Claude → Groq-style → OpenRouter → Cerebras) | Free tier — это rate limit и отсутствие SLA; геоблок/прерывание провайдера не редкость, поэтому есть retry+backoff и явный геоблок-шорткат |
| `mark_seen` до AI-матчинга, не после | Идемпотентный рестарт — повторный запуск не пере-парсит и не пере-фильтрует уже виденное | Вакансия, на которой упал AI-вызов, не ретраится в следующем цикле — осознанный компромисс, не баг |
| Polling-бот в daemon-потоке внутри того же контейнера, не webhook | Не нужен публичный HTTPS-эндпоинт — бот работает за любым NAT/firewall без доп. инфраструктуры | Поток и scheduler делят жизненный цикл контейнера — нельзя перезапустить независимо |
| SQLite, не Postgres/Redis | Один писатель, один контейнер, датасет — десятки МБ; внешняя БД добавила бы зависимость без выгоды на этом масштабе | Не масштабируется горизонтально — не проблема для personal-tool на одном VPS |
| Кнопка "Сохранить" удалена, не доделана | `callback_data`-кнопка была в проде без обработчика (находка аудита) — нет реального воркфлоу, который требует "сохранённых" вакансий отдельно от Telegram-истории чата | Если такой воркфлоу понадобится — фича пишется с нуля, а не реанимируется из мёртвого кода в `storage.py` |
