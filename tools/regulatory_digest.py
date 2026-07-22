"""Еженедельный дайджест регуляторики/рынка РФ-крипты в Telegram.

Тянет RSS РФ-крипто-СМИ, фильтрует по РФ-регуляторным сигналам за N дней и шлёт
топ-K заголовков через notifier бота. Цель — ловить «волну найма»: новая лицензия/
реестр/допуск/этап цифрового рубля = сигнал скорого спроса на ops/support/AML
(см. docs/RU_CRYPTO_MARKET_MAP.md).

Запуск (внутри контейнера, использует env бота):
    docker exec job-hunter-job-hunter-1 python /app/tools/regulatory_digest.py
Расписание — host-cron на Senko (еженедельно), обёртка /usr/local/bin/regulatory_digest.sh.
Тест без отправки: python /app/tools/regulatory_digest.py --dry
"""
import datetime as dt
import sys
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

sys.path.insert(0, "/app")

# RSS-источники (список — добавить bits.media/cbr при валидации через /sources)
FEEDS = [
    ("ForkLog", "https://forklog.com/feed/"),
]

# РФ-регуляторные/рыночные сигналы (substring, lowercase). Матч ТОЛЬКО по
# заголовку — описание слишком широко (тянуло Galaxy/quantum, Пакистан и пр.).
# Фокус на России, отсекает глобальный крипто-шум.
KEYWORDS = [
    "росси", " рф", "банк россии", "цб рф", "госдума", "минфин", "минцифры",
    "росфинмониторинг", "цифровой рубл", "цифрового рубля", "эпр",
    "майнинг", "обменник", "криптобирж", "реестр", "легализ", "лицензи",
    "налог на крипт", "запрет крипт", "оборот криптовалют", "цифровых активов",
]
DAYS = 7
MAX_ITEMS = 8
_UA = {"User-Agent": "Mozilla/5.0 (compatible; job-hunter-digest/1.0)"}


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    return urllib.request.urlopen(req, timeout=25).read()


def _parse_feed(name: str, url: str, cutoff: dt.datetime) -> list[tuple]:
    out: list[tuple] = []
    try:
        root = ET.fromstring(_fetch(url))
    except Exception as e:  # noqa: BLE001 — источник может лечь, дайджест не должен падать
        print(f"[warn] {name}: {type(e).__name__}: {e}", file=sys.stderr)
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "")
        pub = item.findtext("pubDate") or ""
        when = None
        try:
            d = parsedate_to_datetime(pub)
            when = d.astimezone(dt.timezone.utc).replace(tzinfo=None) if d.tzinfo else d
        except Exception:  # noqa: BLE001
            when = None
        if when and when < cutoff:
            continue
        _ = desc  # описание не матчим (слишком широко) — оставлено для будущего
        text = title.lower()
        if any(k in text for k in KEYWORDS):
            out.append((when, name, title, link))
    return out


def build_digest() -> str | None:
    cutoff = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=DAYS)
    items: list[tuple] = []
    for name, url in FEEDS:
        items += _parse_feed(name, url, cutoff)
    seen: set[str] = set()
    uniq: list[tuple] = []
    for it in sorted(items, key=lambda x: (x[0] or dt.datetime.min), reverse=True):
        if it[3] in seen:
            continue
        seen.add(it[3])
        uniq.append(it)
    uniq = uniq[:MAX_ITEMS]
    if not uniq:
        return None
    lines = ["🏛 <b>РФ-крипто: регуляторика за неделю</b>", ""]
    for when, name, title, link in uniq:
        day = when.strftime("%d.%m") if when else "—"
        lines.append(f"• {day} <a href=\"{link}\">{title}</a>")
    lines.append("")
    lines.append("Новая лицензия/реестр/допуск/этап цифрового рубля = сигнал скорого найма.")
    return "\n".join(lines)


def main() -> None:
    dry = "--dry" in sys.argv
    digest = build_digest()
    if digest is None:
        digest = ("🏛 <b>РФ-крипто: регуляторика за неделю</b>\n"
                  "Значимых регуляторных новостей за неделю не найдено.")
    if dry:
        print(digest)
        return
    from src.bot.notifier import send_text
    send_text(digest)


if __name__ == "__main__":
    main()
