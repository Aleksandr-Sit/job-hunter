# Задача для Claude Code: встроить критерии в проект (безопасно)

Открой панель Claude Code на `My Project`, переключи режим на **Plan**,
и вставь промпт между линиями. Он работает на отдельной ветке, показывает дифф
и ничего не пушит, пока ты не проверишь локально.

---

```
Проект job-hunter уже работает (Docker + VPS Senko). Структура: config/ (settings.yaml,
sources.yaml, profile/) и src/matcher/pre_filter.py, недавно доработанный под
CIS/русскоязычные вакансии. Это рабочий пайплайн — не дублируй и не ломай его.

Сначала прочитай: job-hunter/CLAUDE.md, job-hunter/PROFILE.md,
job-hunter/docs/TARGET_CRITERIA.md, job-hunter/docs/target_criteria_REFERENCE.py (референс),
и существующие config/profile/*, config/settings.yaml, config/sources.yaml,
src/matcher/pre_filter.py.

Задача (в моём стиле, без параллельных модулей):
1) Приведи рантайм-профиль config/profile/ в соответствие с PROFILE.md.
   PROFILE.md — мастер; второго источника правды не создавай.
2) Критерии двух ролей (Crypto/Web3 Operations и Web3 Support) из
   target_criteria_REFERENCE.py вынеси в YAML (config/settings.yaml или
   новый config/criteria.yaml) в стиле существующих конфигов: целевые должности,
   must/boost/стоп-слова, штраф за сильный английский и за продажи/лидерство, пороги.
3) Логику (gate-предфильтр + взвешенный балл + reasons) встрой в
   src/matcher/pre_filter.py. target_criteria_REFERENCE.py как модуль НЕ подключай.
4) Уже сделанную CIS/русскоязычную часть переиспользуй, не пересоздавай.
5) LLM-рубрику из TARGET_CRITERIA.md добавь в промпт AI-матчинга.

Работай на ветке feature/role-criteria. Сначала покажи план и дифф; применяй
после моего «ок». Ничего не пушь и VPS не трогай, пока я не проверю локально
через docker-compose up --build.
```

---

## Гарантии безопасности (что этот промпт НЕ делает)

- не трогает рабочий Docker / парсеры / деплой;
- не дублирует уже сделанную CIS/русскоязычную доработку;
- не создаёт второй источник правды (PROFILE.md — мастер);
- работает на отдельной ветке `feature/role-criteria` — основной код в `main` не меняется до твоего merge;
- не пушит и не трогает VPS, пока ты не проверишь локально.

## После того как Claude применил изменения

1. Локально проверь: `docker-compose up --build` — бот стартует, матчинг отрабатывает, в карточках Telegram видны `reasons`.
2. Если что-то не так — откати: `git checkout main` (ветку можно удалить). Рабочая версия не пострадала.
3. Если всё ок — merge ветки в `main`, затем деплой как обычно: push → ssh на Senko → `git pull` → rebuild контейнера.
