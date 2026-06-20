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

Файл: `src/matcher/claude_matcher.py` → функция `_make_system_prompt()`

Структура системного промта:
1. Роль и задача
2. Шкала оценок (0-100) — **изменяй здесь если скор некалиброван**
3. Формат JSON-ответа
4. Профиль кандидата (кэшируется)

Пример добавления критерия:
```python
"- Weight salary match heavily: if salary is below candidate minimum, score max 50\n"
```

### Обновить профиль

- `config/profile/resume.md` — резюме (кэшируется в Claude, обновится автоматически)
- `config/profile/skills.json` — навыки и уровни
- `config/profile/preferences.json` — зарплата, роли, стек, формат работы

После изменения профиля **не нужно** перезапускать — кэш обновится сам.

### Тест матчинга

```bash
python -m src.matcher.gemini_matcher --test
```

Запускает 3 тестовые вакансии и показывает score + reasoning.

### Оптимизация расхода контекста

Текущая стратегия (менять осторожно):
- Батч: 10 вакансий за запрос (`_BATCH_SIZE = 10` в claude_matcher.py)
- Профиль кэшируется через `cache_control: ephemeral`
- Pre-filter отсекает нерелевантные до Claude (`src/matcher/pre_filter.py`)
- Результаты кэшируются в SQLite — одна вакансия не оценивается дважды

**Why:** Gemini 2.0 Flash бесплатен до 1M токенов/день.
SQLite-кэш результатов — каждая вакансия оценивается один раз и больше не тратит лимит.
