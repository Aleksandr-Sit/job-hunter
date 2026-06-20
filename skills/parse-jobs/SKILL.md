---
name: parse-jobs
description: >
  Use when adding a new job board parser to job-hunter project, debugging an existing parser,
  or when a parser stops returning results (site structure changed). Covers HH.ru, Telegram,
  and any web scraping parser in src/parsers/.
metadata:
  type: project-skill
---

# parse-jobs

## When to use
- "Добавь парсер для [сайт]"
- "Парсер [имя] перестал работать"
- "Сайт изменил структуру, нужно починить"
- "Сколько вакансий пришло из источника X?"

## Instructions

### Добавление нового парсера

1. Создай файл `src/parsers/web/<name>.py`
2. Унаследуй `BaseParser` из `src/parsers/base.py`
3. Реализуй `parse(self) -> list[Job]`
4. Каждой вакансии дай уникальный `id` с префиксом источника: `f"{prefix}_{uid}"`
5. Заполни обязательные поля: `id`, `title`, `company`, `description`, `url`, `source`
6. Зарегистрируй парсер в `src/scheduler.py` в функции `_build_parsers()`
7. Добавь флаг `enabled` в `config/settings.yaml` → `parsers:`
8. Протестируй: `python -m src.parsers.web.<name>`

### Структура парсера

```python
class MyParser(BaseParser):
    name = "mysite"

    def parse(self) -> list[Job]:
        # 1. Fetch (requests.get или API)
        # 2. Parse (BeautifulSoup или json)  
        # 3. Return list[Job]
        ...
```

### Отладка

- Запусти парсер напрямую: `python -m src.parsers.web.<name>`
- Проверь логи: `data/logs/job-hunter.log`
- Если сайт изменил верстку — обнови CSS-селекторы
- Если блокирует — смени User-Agent или добавь `time.sleep(1)` между запросами

### Шаблон парсера

Используй `tools/template_parser.py` как стартовую точку.

**Why:** Все парсеры следуют одному интерфейсу `BaseParser.parse() → list[Job]`,
это позволяет scheduler подключать их без изменения основного кода.
