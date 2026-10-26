"""Разбор переписки одного чата: что в нём ждёт ответа.

Порт `hh_chats.js` из скилла vacancy-apply (проверен 26.10.2026 на 79 чатах).
Чистые функции без сети — их проверяют тесты на подменённых ответах chatik.

Группы:
  robot   — вопросы робота-рекрутёра после моего последнего сообщения (повторы
            склеены по тексту, отмечено, ушёл ли робот из чата);
  ask     — сообщения людей после моего последнего: вопрос, ссылка или приглашение;
  news    — остальные сообщения людей (шаблон «рассмотрим», ответ про деньги);
  hh_warn — служебное от hh, кроме известных шумовых фраз;
  отказ   — чат закрыт, ответа не ждут; учитывается только счётчиком.
"""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

LINK = re.compile(r"https?://\S+|t\.me/\S+")
# Служебные фразы hh, которые не требуют действий.
HH_NOISE = re.compile(r"Работодатель подготовил|Спасибо за ответы|ответьте на несколько вопросов")
_SERVICE_TYPES = {"PARTICIPANT_JOINED", "PARTICIPANT_LEFT"}
_SAMARA = timezone(timedelta(hours=4))


def flat(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def samara_time(iso: str) -> str:
    """'2026-10-26T14:05:00.000+03:00' → '26.10 15:05' (по Самаре, UTC+4).
    API отдаёт МСК, а окно чата hh и владелец живут по самарскому времени."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=3)))
        return dt.astimezone(_SAMARA).strftime("%d.%m %H:%M")
    except (ValueError, AttributeError):
        s = iso or ""
        return f"{s[8:10]}.{s[5:7]} {s[11:16]}"


def since_date(days: int, now: datetime | None = None) -> str:
    """Граница окна строкой 'YYYY-MM-DD': время в API — ISO, строки сравнимы."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=days)).strftime("%Y-%m-%d")


def _name(m: dict) -> str:
    return (m.get("participantDisplay") or {}).get("name") or ""


def is_hh(m: dict) -> bool:
    return _name(m) == "hh.ru"


def is_robot(m: dict) -> bool:
    return bool((m.get("participantDisplay") or {}).get("isBot")) and not is_hh(m)


def _state(m: dict) -> str:
    return (m.get("workflowTransition") or {}).get("applicantState") or ""


@dataclass
class RobotQuestion:
    key: str
    text: str
    buttons: list
    first_time: str
    repeats: int
    left: bool              # робот вышел — кнопки пропали, отвечать текстом


@dataclass
class Message:
    key: str
    id: int
    time: str
    author: str
    text: str
    invite: bool = False


@dataclass
class ChatView:
    chat_id: int
    employer: str
    vacancy: str
    vacancy_id: str
    write_allowed: bool
    messages: list                       # вся переписка, по времени
    my_id: str
    discard_time: str = ""               # непусто — отказ, ответа не ждут
    robot: list = field(default_factory=list)
    ask: list = field(default_factory=list)
    news: list = field(default_factory=list)
    hh_warn: list = field(default_factory=list)

    @property
    def discard_key(self) -> str:
        return f"discard:{self.chat_id}"

    @property
    def chat_url(self) -> str:
        return f"https://hh.ru/chat/{self.chat_id}"

    @property
    def vacancy_url(self) -> str:
        return f"https://hh.ru/vacancy/{self.vacancy_id}" if self.vacancy_id else ""


def robot_key(chat_id, text: str) -> str:
    return f"robot:{chat_id}:{hashlib.md5(text.encode('utf-8')).hexdigest()[:12]}"


def in_window(chat_item: dict, since: str) -> bool:
    if chat_item.get("type") != "NEGOTIATION":
        return False
    return ((chat_item.get("lastMessage") or {}).get("creationTime") or "") >= since


def analyze_chat(chat_item: dict, display: dict, data: dict, since: str) -> ChatView:
    chat = data.get("chat") or {}
    chat_id = chat_item.get("id") or chat.get("id")
    me = str(chat.get("currentParticipantId") or chat_item.get("currentParticipantId") or "")
    items = sorted((chat.get("messages") or {}).get("items") or [],
                   key=lambda m: m.get("creationTime") or "")
    disp = display.get(str(chat_id)) or display.get(chat_id) or {}
    vac_ids = (chat_item.get("resources") or chat.get("resources") or {}).get("VACANCY") or []
    write = ((data.get("chatStates") or {}).get("writeMessageState") or {}).get("allowed", True)
    view = ChatView(chat_id=chat_id, employer=disp.get("subtitle") or "?",
                    vacancy=disp.get("title") or "?",
                    vacancy_id=str(vac_ids[0]) if vac_ids else "",
                    write_allowed=bool(write), messages=items, my_id=me)

    # Отказ — по ПОСЛЕДНЕМУ переходу статуса: если после отказа снова пригласили,
    # чат живой (hh_chats.js смотрел на любой отказ в истории).
    transitions = [m for m in items if _state(m)]
    if transitions and _state(transitions[-1]) == "DISCARD":
        view.discard_time = transitions[-1].get("creationTime") or ""
        return view

    last_mine = -1
    for i, m in enumerate(items):
        if str(m.get("participantId")) == me:
            last_mine = i
    after = items[last_mine + 1:]

    # Как в hh_chats.js: любой выход участника после моего сообщения. Люди из
    # чатов откликов не выходят, выходит робот («покинул чат»).
    left = any(m.get("type") == "PARTICIPANT_LEFT" for m in after)
    questions: dict[str, RobotQuestion] = {}
    for m in after:
        buttons = [b.get("text") for b in ((m.get("actions") or {}).get("text_buttons") or [])
                   if b.get("text")]
        if not (is_robot(m) and buttons):
            continue
        q = flat(m.get("text"))
        if q in questions:
            questions[q].repeats += 1
        else:
            questions[q] = RobotQuestion(key=robot_key(chat_id, q), text=q, buttons=buttons,
                                         first_time=m.get("creationTime") or "",
                                         repeats=1, left=False)
    for q in questions.values():
        q.left = left
    view.robot = list(questions.values())

    for m in after:
        t = flat(m.get("text"))
        if not t or is_robot(m) or m.get("type") in _SERVICE_TYPES:
            continue
        msg = Message(key=f"msg:{m.get('id')}", id=m.get("id"), time=m.get("creationTime") or "",
                      author=_name(m) or "?", text=t,
                      invite=_state(m) == "INTERVIEW")
        if is_hh(m):
            if not HH_NOISE.search(t):
                view.hh_warn.append(msg)
            continue
        if msg.time < since:
            continue
        if msg.invite or "?" in t or LINK.search(t):
            view.ask.append(msg)
        else:
            view.news.append(msg)
    return view
