# CLAUDE.md — job-hunter (Docker + VPS Senko)

## Что это
Пайплайн: парсит вакансии, скорит их и шлёт релевантные в Telegram.
Работает в Docker, деплоится на VPS Senko через git push → ssh pull → rebuild контейнера.

## Структура (важное)
- `config/settings.yaml`, `config/sources.yaml` — конфигурация парсинга и поиска.
- `config/profile/` — рантайм-профиль кандидата, который **читает бот**.
- `src/` — код; `src/matcher/pre_filter.py` — предфильтр (недавно доработан под CIS/русскоязычные вакансии).
- `docker-compose.yml`, `Dockerfile`, `requirements.txt`, `.env` — окружение.
- `PROFILE.md` — человекочитаемый мастер-профиль (источник правды о кандидате).
- `docs/` — справочники: `career_consultant_prompt.md`, `TARGET_CRITERIA.md`, `target_criteria_REFERENCE.py`, `AUDIT.md`, `project_audit_prompt.md`.

## Правила работы
- **PROFILE.md — мастер.** `config/profile/` держи в соответствии с ним; второй источник правды не создавай.
- Рабочий пайплайн (Docker, парсеры, `pre_filter.py`) **не ломать**; уже сделанную CIS/русскоязычную доработку **не дублировать**.
- Крупные изменения — на отдельной ветке, через **Plan mode**, с диффом. Перед пушем проверять локально `docker-compose up --build`. На VPS файлы руками не править.
- Критерии целевых вакансий (четыре роли: **Crypto/Web3 Operations**, **Web3 Support**, **AI Automation** — индустриально-независимая, **Web3 QA** — ручное тестирование) живут в `config/criteria.yaml`, логика — в `src/matcher/pre_filter.py`. `target_criteria_REFERENCE.py` — только эталон, как модуль НЕ подключать.
- **Репозиторий ПУБЛИЧНЫЙ (портфолио).** Поэтому личные данные в него не коммитятся: `.env`, `PROFILE.md`, `config/profile/*` (кроме `*.example`), `docs/resume/` — всё в `.gitignore`. Профиль доставляется на VPS через `scp` (как `.env`) и подключается bind-mount'ом. ⚠️ После `git reset --hard` на VPS папка `config/profile` пересоздаётся → нужен `docker compose up -d --force-recreate`, иначе bind-mount остаётся на удалённом inode и бот не видит профиль.

## Контекст кандидата
Александр, Самара. Переход из продаж/IT-поддержки в Web3/крипто и AI-автоматизацию.
~6 лет on-chain опыта; с июня 2026 строит AI-автоматизации на Claude Code (пять открытых
портфельных проектов — job-hunter, crypto-trader, smart-money, accumulation-scanner,
beach-volley-coach). Английский A1–A2.
Цель: remote или релокация (Кипр/Греция/Таиланд/Турция/Армения/ОАЭ/Сербия). Детали — в `PROFILE.md`.
