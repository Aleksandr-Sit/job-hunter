"""Сторож чатов hh — запуск по расписанию и вручную.

Что делает за один запуск:
  1. читает все чаты откликов за окно (по умолчанию 30 дней);
  2. по чату, где появился вопрос робота или вопрос/ссылка/приглашение от людей,
     присылает карточку с вариантом ответа (моноширинный блок — копировать с телефона);
  3. прочие новые сообщения людей, служебное hh и число новых отказов — одной сводкой;
  4. каждую позицию — один раз (ключи в таблице hh_chat_notified); нового нет — молчит.

В hh ничего не отправляет (этап 1). Алерт «сессия истекла» — один раз на переход,
«снова работает» — один раз при восстановлении; сбой сети — только после двух
неудачных запусков подряд: ложная тревога хуже её отсутствия.

Ручной запуск в контейнере:
  python -m src.hh_chats.watch              — как по расписанию (шлёт в Telegram)
  python -m src.hh_chats.watch --dry-run    — напечатать, ничего не слать и не помечать
  python -m src.hh_chats.watch --demo       — прислать тестовую карточку из подставных данных
  python -m src.hh_chats.watch --cookies F  — взять cookie из F (проверка алерта)
"""
import argparse
import html
import logging
import time
from pathlib import Path
from typing import Callable, Optional

import yaml

from .. import storage
from .client import ChatikClient, ChatikUnavailable, SessionExpired, build_session, load_cookies
from .reply import ReplyDraft, RobotAnswer, generate_reply
from .sweep import ChatView, Message, RobotQuestion, analyze_chat, in_window, samara_time, since_date

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_SETTINGS = _ROOT / "config" / "settings.yaml"
_DEFAULTS = {"since_days": 30, "cookie_path": "config/hh_session", "max_cards": 10}

_ST_SESSION = "hh_chats.session"          # ok | expired
_ST_NET_FAIL = "hh_chats.net_fail"        # неудачных запусков подряд
_ST_FIRST = "hh_chats.first_run_done"
_NET_ALERT_AFTER = 2

_TG_LIMIT = 4000                          # у Telegram 4096, оставляем запас


def load_settings() -> dict:
    try:
        cfg = yaml.safe_load(_SETTINGS.read_text(encoding="utf-8")) or {}
    except OSError:
        cfg = {}
    return {**_DEFAULTS, **(cfg.get("hh_chats") or {})}


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n].rstrip() + "…"


# ── карточка ──────────────────────────────────────────────────────────────────

def format_card(view: ChatView, draft: Optional[ReplyDraft], msg_cap: int = 1200) -> str:
    head = f"💬 <b>Чат hh ждёт ответа</b>\n<b>{_esc(view.employer)}</b> · "
    if view.vacancy_url:
        head += f'<a href="{view.vacancy_url}">{_esc(view.vacancy)}</a>'
    else:
        head += _esc(view.vacancy)
    parts = [head]

    answers = {a.question: a for a in (draft.robot if draft else [])}
    for q in view.robot:
        rep = f", повторён {q.repeats} раза" if q.repeats > 1 else ""
        block = [f"🤖 <b>Робот-рекрутёр</b> · {samara_time(q.first_time)}{rep}",
                 f"«{_esc(_cut(q.text, 700))}»",
                 "Кнопки: " + " · ".join(_esc(b) for b in q.buttons)]
        if q.left:
            block.append("<i>Робот вышел из чата — кнопок нет, ответ только текстом "
                         "в поле «Сообщение» (увидит HR).</i>")
        else:
            block.append("<i>Кнопки живы — ответ нажатием. Без ответа робот повторит "
                         "вопрос и через 1–2 дня уйдёт.</i>")
        a: Optional[RobotAnswer] = answers.get(q.text)
        if a and a.button:
            block.append(f"👉 {'Ответ' if q.left else 'Нажми'}: <code>{_esc(a.button)}</code>")
            if a.reason:
                block.append(f"<i>Почему: {_esc(_cut(a.reason, 300))}</i>")
        elif draft:
            block.append("👉 Модель не выбрала кнопку из списка — реши сам.")
        parts.append("\n".join(block))

    for m in view.ask:
        tag = " · 📅 <b>приглашение</b>" if m.invite else ""
        parts.append(f"👤 <b>{_esc(m.author)}</b> · {samara_time(m.time)}{tag}\n"
                     f"«{_esc(_cut(m.text, msg_cap))}»")

    if draft is None:
        parts.append("✍️ Вариант ответа не получился (сбой AI) — ответь сам.")
    elif draft.reply:
        parts.append("✍️ <b>Вариант ответа</b> (блок копируется нажатием):\n"
                     f"<pre>{_esc(_cut(draft.reply, 1500))}</pre>")
    if draft and (draft.needs_decision or draft.note):
        mark = "⚠️ <b>Решать тебе:</b> " if draft.needs_decision else "ℹ️ "
        parts.append(mark + _esc(_cut(draft.note or "ответ может закрыть отклик", 400)))
    if not view.write_allowed:
        parts.append("⛔ hh закрыл переписку в этом чате — написать текстом нельзя.")

    text = "\n\n".join(parts)
    if len(text) > _TG_LIMIT and msg_cap > 300:
        return format_card(view, draft, msg_cap=300)
    return text[:_TG_LIMIT]


def card_links(view: ChatView) -> list[tuple[str, str]]:
    return [("💬 Открыть чат на hh", view.chat_url), ("📄 Вакансия", view.vacancy_url)]


def format_digest(news: list[tuple[ChatView, Message]], warns: list[tuple[ChatView, Message]],
                  discards: int, first_run: bool, since_days: int) -> str:
    lines = ["📭 <b>Чаты hh — новое без вопросов к тебе</b>"]
    shown = 0
    for view, m in news:
        if shown >= 12:
            lines.append(f"… и ещё {len(news) - shown}")
            break
        lines.append(f'👤 <b>{_esc(view.employer)}</b> — {_esc(_cut(view.vacancy, 60))}: '
                     f'«{_esc(_cut(m.text, 220))}» · <a href="{view.chat_url}">чат</a>')
        shown += 1
    for view, m in warns[:6]:
        lines.append(f'🛈 hh · <b>{_esc(view.employer)}</b>: «{_esc(_cut(m.text, 200))}» · '
                     f'<a href="{view.chat_url}">чат</a>')
    if discards:
        span = f"за {since_days} дн." if first_run else "с прошлой проверки"
        lines.append(f"❌ Отказов {span}: {discards}")
    return "\n".join(lines)[:_TG_LIMIT]


# ── состояние и алерты ────────────────────────────────────────────────────────

def _session_problem(reason: str, send: Callable[[str], bool]) -> None:
    if storage.state_get(_ST_SESSION, "ok") == "expired":
        logger.warning("Чаты hh: сессия всё ещё недействительна (%s) — алерт уже был", reason)
        return
    ok = send("⚠️ <b>Сессия hh истекла — обнови вход</b>\n"
              f"Сторож чатов не может читать переписку ({_esc(reason)}).\n"
              "Экспортируй cookie hh заново и положи файл на сервер. Пока не обновишь — "
              "проверяй чаты hh сам, особенно вопросы робота-рекрутёра.")
    if ok:
        storage.state_set(_ST_SESSION, "expired")
    logger.warning("Чаты hh: сессия недействительна (%s), алерт %s", reason,
                   "отправлен" if ok else "НЕ ушёл")


def _network_problem(reason: str, send: Callable[[str], bool]) -> None:
    fails = int(storage.state_get(_ST_NET_FAIL, "0") or 0) + 1
    storage.state_set(_ST_NET_FAIL, str(fails))
    logger.warning("Чаты hh: chatik недоступен (%s), неудачных запусков подряд: %d", reason, fails)
    if fails == _NET_ALERT_AFTER:
        send(f"⚠️ <b>chatik.hh.ru не отвечает</b> {fails} запуска подряд ({_esc(reason)}).\n"
             "Сессия, скорее всего, жива — это сбой сети или hh. Чаты пока проверь сам.")


def _session_ok(send: Callable[[str], bool]) -> None:
    if storage.state_get(_ST_SESSION, "ok") == "expired":
        send("✅ <b>Сессия hh снова работает</b> — сторож чатов читает переписку.")
    storage.state_set(_ST_SESSION, "ok")
    storage.state_set(_ST_NET_FAIL, "0")


# ── запуск ────────────────────────────────────────────────────────────────────

def _default_send(text: str) -> bool:
    from ..bot.notifier import send_text
    return send_text(text)


def _default_card(text: str, links: list) -> bool:
    from ..bot.notifier import send_card
    return send_card(text, links)


def _vacancy_text(view: ChatView) -> str:
    if not view.vacancy_id:
        return ""
    try:
        from ..parsers.hh_enrich import fetch_description
        return fetch_description(view.vacancy_url)
    except Exception as e:
        logger.debug("Чаты hh: описание вакансии %s не скачалось (%s)", view.vacancy_id, e)
        return ""


def run_watch(settings: Optional[dict] = None, *, dry_run: bool = False,
              cookie_path: Optional[str] = None,
              client: Optional[ChatikClient] = None,
              send: Callable[[str], bool] = _default_send,
              send_card: Callable[[str, list], bool] = _default_card,
              reply_fn: Callable[[ChatView, str], Optional[ReplyDraft]] = generate_reply,
              vacancy_fn: Callable[[ChatView], str] = _vacancy_text) -> dict:
    """Один проход сторожа. Возвращает сводку для лога и тестов."""
    cfg = {**_DEFAULTS, **(settings or load_settings())}
    since = since_date(int(cfg["since_days"]))
    summary = {"status": "ok", "chats": 0, "cards": 0, "digest": 0, "skipped": 0}
    storage.init_db()

    def alert(text: str) -> bool:
        if dry_run:
            print("[алерт]", text)
            return False
        return send(text)

    if client is None:
        path = Path(cookie_path or cfg["cookie_path"])
        if not path.is_absolute():
            path = _ROOT / path
        try:
            client = ChatikClient(build_session(load_cookies(path)))
        except SessionExpired as e:
            summary["status"] = "session"
            if not dry_run:
                _session_problem(str(e), alert)
            else:
                print("[сессия]", e)
            return summary

    try:
        chats, display = client.list_chats()
        views: list[ChatView] = []
        for c in chats:
            if not in_window(c, since):
                continue
            try:
                data = client.chat_data(c["id"])
            except ChatikUnavailable as e:
                summary["skipped"] += 1
                logger.warning("Чаты hh: чат %s пропущен (%s) — посмотрю в следующий раз",
                               c.get("id"), e)
                continue
            views.append(analyze_chat(c, display, data, since))
    except SessionExpired as e:
        summary["status"] = "session"
        if not dry_run:
            _session_problem(str(e), alert)
        else:
            print("[сессия]", e)
        return summary
    except ChatikUnavailable as e:
        summary["status"] = "network"
        if not dry_run:
            _network_problem(str(e), alert)
        else:
            print("[сеть]", e)
        return summary

    if not dry_run:
        _session_ok(alert)
    if client.rotated:
        logger.info("Чаты hh: hh переустановил cookie (имена): %s",
                    ", ".join(sorted(client.rotated)))
    summary["chats"] = len(views)

    keys = []
    for v in views:
        keys += [q.key for q in v.robot] + [m.key for m in v.ask + v.news + v.hh_warn]
        if v.discard_time and v.discard_time >= since:
            keys.append(v.discard_key)
    known = storage.hh_chat_known(keys)

    # Карточки: чат, где есть НОВЫЙ вопрос робота или вопрос людей.
    cards_left = int(cfg["max_cards"])
    for v in views:
        waiting = [q.key for q in v.robot] + [m.key for m in v.ask]
        if not waiting or all(k in known for k in waiting):
            continue
        if cards_left <= 0:
            logger.info("Чаты hh: потолок карточек за запуск — чат %s в следующий раз", v.chat_id)
            continue
        draft = reply_fn(v, vacancy_fn(v))
        text = format_card(v, draft)
        if dry_run:
            print("=" * 60, "\n", text, "\n", card_links(v))
            ok = False
        else:
            ok = send_card(text, card_links(v))
        cards_left -= 1
        if ok:
            storage.hh_chat_mark([(k, v.chat_id) for k in waiting])
            summary["cards"] += 1
        time.sleep(0 if dry_run else 0.5)

    # Сводка: новые сообщения без вопроса, служебное hh, число новых отказов.
    news = [(v, m) for v in views for m in v.news if m.key not in known]
    warns = [(v, m) for v in views for m in v.hh_warn if m.key not in known]
    discards = [v for v in views
                if v.discard_time and v.discard_time >= since and v.discard_key not in known]
    if news or warns or discards:
        first_run = storage.state_get(_ST_FIRST) != "1"
        text = format_digest(news, warns, len(discards), first_run, int(cfg["since_days"]))
        if dry_run:
            print("=" * 60, "\n", text)
        elif send(text):
            storage.hh_chat_mark([(m.key, v.chat_id) for v, m in news + warns] +
                                 [(v.discard_key, v.chat_id) for v in discards])
            summary["digest"] = len(news) + len(warns) + len(discards)
    if not dry_run:
        storage.state_set(_ST_FIRST, "1")

    logger.info("Чаты hh: прочитано чатов %d (пропущено %d), карточек %d, в сводке %d",
                summary["chats"], summary["skipped"], summary["cards"], summary["digest"])
    return summary


# ── тестовая карточка ─────────────────────────────────────────────────────────

def demo_card() -> tuple[str, list]:
    """Карточка из подставных данных: как выглядит, без hh и без AI."""
    view = ChatView(chat_id=1234567890, employer="ООО «Пример»", vacancy="Специалист поддержки",
                    vacancy_id="100000001", write_allowed=True, messages=[], my_id="1")
    view.robot = [RobotQuestion(key="robot:demo", text="Есть ли у вас опыт работы с клиентами "
                                "от 2 лет?", buttons=["от 2 лет", "до 2 лет"],
                                first_time="2026-10-26T11:05:00.000+03:00", repeats=2, left=False)]
    view.ask = [Message(key="msg:demo", id=1, time="2026-10-26T12:30:00.000+03:00",
                        author="Анна", text="Добрый день! Когда вам удобно созвониться на "
                        "15 минут? Пройдите, пожалуйста, тест: https://example.com/test")]
    draft = ReplyDraft(robot=[RobotAnswer(question=view.robot[0].text, button="от 2 лет",
                                          reason="8+ лет продаж и поддержки клиентов в рознице "
                                                 "и телекоме — это есть в письме")],
                       reply="Добрый день, Анна! Тест пройду сегодня. Созвониться удобно "
                             "завтра с 11:00 до 17:00 по Москве, онлайн.",
                       needs_decision=False, note="")
    return "🧪 <b>ТЕСТ — так выглядит карточка</b>\n\n" + format_card(view, draft), card_links(view)


def main() -> None:
    import sys

    from dotenv import load_dotenv

    from ..log_redact import RedactingFilter
    load_dotenv(_ROOT / ".env")
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    for noisy in ("httpx", "httpcore", "telegram", "urllib3", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    ap = argparse.ArgumentParser(description="Сторож чатов hh (этап 1, только чтение)")
    ap.add_argument("--dry-run", action="store_true", help="напечатать, ничего не слать")
    ap.add_argument("--demo", action="store_true", help="прислать тестовую карточку")
    ap.add_argument("--cookies", help="файл или папка с cookie вместо настроек")
    args = ap.parse_args()

    if args.demo:
        text, links = demo_card()
        print("отправлено" if _default_card(text, links) else "НЕ отправлено")
        return
    print(run_watch(dry_run=args.dry_run, cookie_path=args.cookies))


if __name__ == "__main__":
    main()
