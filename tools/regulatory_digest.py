"""Дайджест регуляторики РФ-крипты в Telegram: ежедневный сбор, недельная отправка.

Цель — ловить «волну найма»: новая лицензия/реестр/допуск/этап цифрового рубля =
сигнал скорого спроса на ops/support/AML (см. docs/RU_CRYPTO_MARKET_MAP.md).

⚠️ Почему сбор и отправка разнесены. RSS отдаёт только 30 ПОСЛЕДНИХ новостей: у
ForkLog это ~27 часов, у Bits.media ~52. Скрипт запускался раз в неделю и
фильтровал «за 7 дней», то есть видел одни сутки из семи и стабильно писал
«новостей не найдено» без единой ошибки (ревизия 17.09.2026). Теперь сбор идёт
каждый день в файл `data/regulatory_digest.jsonl` (дедуп по ссылке), а отправка
раз в неделю берёт накопленное.

Запуск (внутри контейнера, использует env бота):
    python /app/tools/regulatory_digest.py --collect     # ежедневно
    python /app/tools/regulatory_digest.py --send        # раз в неделю
    python /app/tools/regulatory_digest.py --send --dry  # проверка без отправки
Расписание — host-cron на Senko, обёртка /usr/local/bin/regulatory_digest.sh.
"""
import argparse
import datetime as dt
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, "/app")

# Обёртка на хосте скармливает этот файл контейнеру через stdin (`python -`),
# чтобы правки доезжали git pull'ом без пересборки образа. В таком режиме
# `__file__` НЕ определён — отсюда запасной путь.
_ROOT = (Path(globals()["__file__"]).resolve().parent.parent
         if "__file__" in globals() else Path("/app"))
STORE = _ROOT / "data" / "regulatory_digest.jsonl"

# RSS-источники
FEEDS = [
    ("ForkLog", "https://forklog.com/feed/"),
    # Проба с VPS 15.09.2026: 200, 30 новостей. /rss/ и /feed/ — 404.
    ("Bits.media", "https://bits.media/rss2/"),
]

# РФ-регуляторные/рыночные сигналы. Матч ТОЛЬКО по заголовку — описание слишком
# широко (тянуло Galaxy/quantum, Пакистан и пр.).
# Это СТЕМЫ: совпадают с началом слова («росси» → «Россия», «российский»).
KEYWORDS = [
    "росси", "рф", "банк россии", "цб рф", "госдума", "минфин", "минцифры",
    "росфинмониторинг", "цифровой рубл", "цифрового рубля", "эпр",
    "майнинг", "обменник", "криптобирж", "реестр", "легализ", "лицензи",
    "налог на крипт", "запрет крипт", "оборот криптовалют", "цифровых активов",
    # 15.09.2026, RU_CRYPTO_MARKET_MAP §6: язык 282-ФЗ и триггеры волн найма —
    # реестр обменников/депозитариев, допуск неквалов, запуски у банков и брокеров.
    "цифровых валют", "цифровой валют", "цифровые валют", "депозитари", "282-фз",
    "криптоброкер", "неквал", "цфа",
    "сбер", "т-банк", "альфа-банк", "втб", "мосбирж", "спб бирж", "финам",
    "совкомбанк", "газпромбанк",
]

# Ключи, которым нужен свой шаблон: стем ловил чужое слово.
SPECIAL = {
    # «сбер» ловил «сбережения» (проверено 17.09.2026). Разрешаем само слово
    # и «Сбербанк»/«Сбербанка», но не «сбережения».
    "сбер": r"сбер(банк\w*)?(?![а-яё])",
}

DAYS = 7
MAX_ITEMS = 8
_UA = {"User-Agent": "Mozilla/5.0 (compatible; job-hunter-digest/1.0)"}


def _compile_keywords() -> re.Pattern:
    """Стемы с ГРАНИЦЕЙ СЛЕВА: «рф» не должно ловиться внутри «Смарф»."""
    parts = [SPECIAL.get(k, re.escape(k)) for k in KEYWORDS]
    return re.compile(r"(?<![а-яёa-z])(?:" + "|".join(parts) + ")", re.IGNORECASE)


_KEY_RE = _compile_keywords()


def matches(title: str) -> bool:
    return bool(_KEY_RE.search(title or ""))


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    return urllib.request.urlopen(req, timeout=25).read()


def _parse_feed(name: str, url: str) -> list[dict]:
    out: list[dict] = []
    try:
        root = ET.fromstring(_fetch(url))
    except Exception as e:  # noqa: BLE001 — источник может лечь, дайджест не должен падать
        print(f"[warn] {name}: {type(e).__name__}: {e}", file=sys.stderr)
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not link or not matches(title):
            continue
        when = None
        try:
            d = parsedate_to_datetime(item.findtext("pubDate") or "")
            when = (d.astimezone(dt.timezone.utc) if d.tzinfo else d).replace(tzinfo=None)
        except Exception:  # noqa: BLE001
            when = None
        out.append({"source": name, "title": title, "link": link,
                    "published": when.isoformat() if when else "",
                    "collected_at": dt.datetime.now(dt.timezone.utc).replace(
                        tzinfo=None).isoformat()})
    return out


def load_store() -> list[dict]:
    if not STORE.exists():
        return []
    rows = []
    for line in STORE.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def collect() -> tuple[int, int]:
    """Сбор за сутки: дописывает НОВЫЕ совпадения в хранилище. -> (новых, всего)."""
    known = {r.get("link") for r in load_store()}
    fresh = []
    for name, url in FEEDS:
        for row in _parse_feed(name, url):
            if row["link"] in known:
                continue
            known.add(row["link"])
            fresh.append(row)
    if fresh:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        with STORE.open("a", encoding="utf-8") as f:
            for row in fresh:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(fresh), len(known)


def _when(row: dict) -> dt.datetime:
    for key in ("published", "collected_at"):
        raw = row.get(key) or ""
        try:
            return dt.datetime.fromisoformat(raw)
        except ValueError:
            continue
    return dt.datetime.min


def build_digest(days: int = DAYS) -> str | None:
    cutoff = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=days)
    rows = [r for r in load_store() if _when(r) >= cutoff]
    rows.sort(key=_when, reverse=True)
    seen, uniq = set(), []
    for r in rows:
        if r.get("link") in seen:
            continue
        seen.add(r.get("link"))
        uniq.append(r)
    uniq = uniq[:MAX_ITEMS]
    if not uniq:
        return None
    lines = ["🏛 <b>РФ-крипто: регуляторика за неделю</b>", ""]
    for r in uniq:
        when = _when(r)
        day = when.strftime("%d.%m") if when != dt.datetime.min else "—"
        # Экранируем: «<» в заголовке роняет отправку с Telegram 400, а ошибка
        # раньше глоталась молча — дайджест просто не приходил.
        title = html.escape(r.get("title") or "")
        link = html.escape(r.get("link") or "", quote=True)
        lines.append(f'• {day} <a href="{link}">{title}</a>')
    lines.append("")
    lines.append("Новая лицензия/реестр/допуск/этап цифрового рубля = сигнал скорого найма.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--collect", action="store_true", help="ежедневный сбор в хранилище")
    ap.add_argument("--send", action="store_true", help="недельная отправка из накопленного")
    ap.add_argument("--dry", action="store_true", help="печатать вместо отправки")
    a = ap.parse_args()
    if not a.collect and not a.send:       # обратная совместимость со старым cron
        a.collect = a.send = True

    if a.collect:
        fresh, total = collect()
        print(f"[collect] новых: {fresh}, всего в хранилище: {total}")

    if not a.send:
        return 0

    digest = build_digest()
    if digest is None:
        digest = ("🏛 <b>РФ-крипто: регуляторика за неделю</b>\n"
                  "Значимых регуляторных новостей за неделю не найдено.")
    if a.dry:
        print(digest)
        return 0

    from src.bot.notifier import send_text
    ok = send_text(digest)
    print(f"[send] отправлено: {bool(ok)}")
    # Ненулевой код возврата виден в логе cron: молчащий дайджест был незаметен.
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
