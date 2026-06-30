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
- Критерии целевых вакансий (три роли: **Crypto/Web3 Operations**, **Web3 Support**, **AI Automation** — индустриально-независимая) живут в `config/criteria.yaml`, логика — в `src/matcher/pre_filter.py`. `target_criteria_REFERENCE.py` — только эталон, как модуль НЕ подключать.
- `.env` — в `.gitignore`. `config/profile/` боту нужен на VPS, поэтому **коммитится**; репозиторий держать приватным.

## Контекст кандидата
Александр, Самара. Переход из продаж/IT-поддержки в Web3/крипто и AI-автоматизацию.
~6 лет on-chain опыта; с июня 2026 строит AI-автоматизации на Claude Code (два рабочих
портфельных проекта — job-hunter, crypto-trader). Английский A1–A2.
Цель: remote или релокация (Кипр/Греция/Таиланд/Турция/Армения/ОАЭ/Сербия). Детали — в `PROFILE.md`.
