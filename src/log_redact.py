"""Вырезание секретов из строк лога.

Почему фильтр, а не список «шумных» логгеров.

15.08.2026 при построении воронки обнаружилось, что `httpx` на уровне INFO печатает
URL запроса целиком, а у Telegram-бота токен лежит ВНУТРИ URL
(`/bot<TOKEN>/getUpdates`). Polling идёт раз в 10 секунд — секрет писался в файл
примерно 8 600 раз в сутки. Первым решением было перевести `httpx` на WARNING, но
тут же нашёлся второй логгер с другим именем (`httpx2`), делающий то же самое.

Отсюда правило: перечисление логгеров — не защита, потому что список всегда неполон.
Защита — фильтр на самом хендлере, который срабатывает независимо от того, какая
библиотека и под каким именем пишет строку.
"""
import logging
import re

# /bot123456:AA... — токен Telegram внутри URL
_TG_TOKEN = re.compile(r"/bot\d{6,}:[A-Za-z0-9_-]{20,}")
# api_key=..., token=..., access_token=... в query-строке
_QUERY_SECRET = re.compile(
    r"((?:api[_-]?key|token|access[_-]?token|secret|password|passwd)=)[^&\s\"']+",
    re.IGNORECASE,
)
# Authorization: Bearer <...> и ключи вида csk-/sk-
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{16,}", re.IGNORECASE)
_PREFIXED_KEY = re.compile(r"\b((?:csk|sk|pk|ghp|gho|xox[abps])[-_])[A-Za-z0-9]{16,}")

_MASK = "***REDACTED***"


def redact(text: str) -> str:
    """Заменяет известные формы секретов на маску. Возвращает исходный тип str."""
    if not text:
        return text
    text = _TG_TOKEN.sub("/bot" + _MASK, text)
    text = _QUERY_SECRET.sub(r"\1" + _MASK, text)
    text = _BEARER.sub(r"\1" + _MASK, text)
    text = _PREFIXED_KEY.sub(r"\1" + _MASK, text)
    return text


class RedactingFilter(logging.Filter):
    """Чистит и само сообщение, и аргументы форматирования.

    Аргументы важны отдельно: `logger.info("HTTP %s", url)` кладёт URL в
    `record.args`, а не в `record.msg`, поэтому чистка одного `msg` его пропустит.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: redact(v) if isinstance(v, str) else v
                               for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(redact(a) if isinstance(a, str) else a
                                    for a in record.args)
        return True
