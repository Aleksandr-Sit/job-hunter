---
name: notify-telegram
description: >
  Use when changing how job notifications look in Telegram: message format,
  emojis, what info to show/hide, button labels, or when adding new notification
  types (weekly digest, urgency alerts). Also use when the bot stops sending messages.
metadata:
  type: project-skill
---

# notify-telegram

## When to use
- "Измени формат сообщения в боте"
- "Добавь кнопку [действие]"
- "Убери зарплату из уведомления"
- "Бот не отправляет сообщения"
- "Добавь еженедельный дайджест"
- "Хочу видеть больше/меньше деталей"

## Instructions

### Изменить формат сообщения

Файл: `src/bot/formatter.py` → функция `format_job_message()`

Текущий формат:
```
{emoji} {title} — {score}/100

🏢 {company}
💰 {salary}
📍 {source} • Remote

📋 {description[:400]}

✅ Почему подходит:
  • {why_fits}

⚠️ Обратить внимание:
  • {watch_out}

💡 {recommendation}
```

Telegram поддерживает: `<b>bold</b>`, `<i>italic</i>`, `<code>code</code>`, `<a href="url">link</a>`

### Изменить кнопки

Файл: `src/bot/notifier.py` → функция `_make_keyboard()`

Добавить кнопку "Откликнуться":
```python
InlineKeyboardButton("📨 Откликнуться", url=job.url)
```

Кнопки с `callback_data` требуют запущенного polling-бота для обработки нажатий.
Кнопки с `url` работают без дополнительной логики.

### Добавить новый тип уведомления

1. Добавь функцию `format_*()` в `src/bot/formatter.py`
2. Добавь функцию `send_*()` в `src/bot/notifier.py`
3. Вызови из `src/scheduler.py`

### Тест отправки

```bash
python -m src.bot.notifier --test
```

Отправляет тестовое сообщение с макетом вакансии.

### Отладка

- Проверь TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env
- Узнать свой CHAT_ID: напиши боту @userinfobot
- Telegram лимит: 4096 символов на сообщение
- Если текст обрезается — уменьши `description[:400]` в formatter.py

**Why:** Форматирование вынесено в отдельный модуль `formatter.py`,
чтобы можно было менять вид сообщений без касания логики отправки.
