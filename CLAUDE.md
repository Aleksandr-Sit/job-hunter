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
- `docs/` — справочники: `career_consultant_prompt.md`, `TARGET_CRITERIA.md`, `MERGE_TASK.md`, `target_criteria_REFERENCE.py`.

## Правила работы
- **PROFILE.md — мастер.** `config/profile/` держи в соответствии с ним; второй источник правды не создавай.
- Рабочий пайплайн (Docker, парсеры, `pre_filter.py`) **не ломать**; уже сделанную CIS/русскоязычную доработку **не дублировать**.
- Крупные изменения — на отдельной ветке, через **Plan mode**, с диффом. Перед пушем проверять локально `docker-compose up --build`. На VPS файлы руками не править.
- Критерии целевых вакансий (две роли: **Crypto/Web3 Operations** и **Web3 Support**) живут в `config/` (YAML), логика — в `src/matcher/pre_filter.py`. `target_criteria_REFERENCE.py` — только эталон, как модуль НЕ подключать.
- `.env` — в `.gitignore`. `config/profile/` боту нужен на VPS, поэтому **коммитится**; репозиторий держать приватным.

## Контекст кандидата
Александр, Самара. Переход из продаж/IT-поддержки в Web3/крипто и AI-автоматизацию.
~5 лет on-chain опыта; AI-автоматизация (Python, Claude Code, n8n, Telegram API). Английский A1–A2.
Цель: remote или релокация (Кипр/Греция/Таиланд/Турция). Детали — в `PROFILE.md`.
