---
name: match-profile
description: >
  Use when tuning job matching logic in job-hunter: adjusting score threshold,
  changing what Claude evaluates, editing prompt, improving batch processing,
  or when scores feel too high/low and need calibration. Also use when updating
  the user's profile (resume, skills, preferences).
metadata:
  type: project-skill
---

# match-profile

## When to use
- "Скор слишком завышен/занижен"
- "Хочу изменить порог с 65 до 75"
- "Обнови моё резюме / навыки / предпочтения"
- "Claude неправильно оценивает вакансии по [критерию]"
- "Добавь в матчинг учёт [параметр]"

## Instructions

### Изменить порог совпадения

В `config/settings.yaml`:
```yaml
matching:
  threshold: 65   # ← изменить здесь
```

### Улучшить prompt матчинга

Файл: `src/matcher/cerebras_matcher.py` → константа `_SYSTEM_INSTRUCTION`

Структура системного промта:
1. Контекст кандидата (профиль, проекты)
2. Три приоритетные роли и порядок их веса
3. Шкала оценок (0-100) — **изменяй здесь если скор некалиброван**
4. Штрафы/обнуление score (английский, лидерские роли, чисто-дев роли и т.д.)
5. Формат JSON-ответа

Пример добавления критерия — дописать строку в блок "Score down for: ...".

### Обновить профиль

- `config/profile/resume.md` — резюме (используется в каждом запросе через `_build_profile_text()`)
- `config/profile/skills.json` — навыки и уровни
- `config/profile/preferences.json` — зарплата, роли, стек, формат работы

После изменения профиля **не нужно** перезапускать — `_build_profile_text()` кэшируется
в памяти процесса (`@lru_cache`), но при следующем запуске контейнера читает файлы заново.

### Тест матчинга

```bash
python -m src.matcher.cerebras_matcher --test
```

Запускает 3 тестовые вакансии и показывает score + reasoning.

### Оптимизация расхода контекста

Текущая стратегия (менять осторожно):
- Батч: 5 вакансий за запрос (`_BATCH_SIZE = 5` в `cerebras_matcher.py`,
  совпадает с `matching.batch_size` в `config/settings.yaml`)
- Профиль кэшируется в памяти процесса через `@lru_cache(maxsize=1)` на
  `_build_profile_text()` — не путать с серверным prompt caching API
- Pre-filter отсекает нерелевантные до AI (`src/matcher/pre_filter.py`)
- Результаты кэшируются в SQLite (`storage.get_cached_match`/`save_match`) —
  одна вакансия не оценивается дважды
- Чекпоинт после каждого батча в `data/matches.jsonl` — безопасный рестарт без
  повторной обработки

**Why:** Cerebras inference бесплатен на free tier (модель задаётся через
`CEREBRAS_MODEL` в `.env`, см. `.env.example`). SQLite-кэш результатов —
каждая вакансия оценивается один раз и больше не тратит лимит.
