"""Вариант ответа в чат hh через языковую модель (те же провайдеры, что у скоринга).

Правила — шаг 8 скилла vacancy-apply: честно и в согласии с письмом; роботу —
буквально одна из его кнопок; ответ, который может закрыть отклик, — только
решением владельца. Модель правила нарушает, поэтому кнопку проверяет код:
не совпала ни с одной настоящей — карточка просит решить самому.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from ..matcher.cerebras_matcher import _build_profile_text, _get_client, available_providers
from .sweep import ChatView, flat, is_hh, is_robot, samara_time

logger = logging.getLogger(__name__)

_TIMEOUT = 60
_MSG_CAP = 1500        # символов на чужое сообщение в промпте
_MINE_CAP = 2500       # своё: первое — сопроводительное, оно важно целиком
_CHAT_CAP = 14000      # вся переписка
_VACANCY_CAP = 3500

_INSTRUCTION = """\
You help a job seeker (full profile below) answer messages in hh.ru application chats.
You get the vacancy, the whole chat (the candidate's FIRST message is his cover letter)
and the list of items waiting for his answer.

Rules:
1. Honesty first. Use ONLY facts from the candidate profile and from his own earlier
   messages. Never invent experience, years, employers, certificates, tools or skills.
2. Stay consistent with what the candidate already wrote. If the cover letter says he
   lacks something, do not claim it now.
3. Robot-recruiter questions come with answer buttons. For EACH question pick exactly ONE
   button, copied character for character, and give a one-line reason in Russian. Pick the
   honest button even when it is less favourable (real case: the letter said "no SaaS
   support experience", so the answer was "до 2 лет", not "от 2 лет").
4. If the robot has left the chat, its buttons no longer work: put the same answer as plain
   text into "reply" so the recruiter sees it.
5. Messages from people: a short polite reply, 1-5 sentences, plain text, no markdown, no
   emoji, no placeholders like [Имя]. Russian, unless they wrote in English — then simple
   English (the candidate's English is A1-A2). Answer exactly what was asked. A link to a
   test, questionnaire or bot: say he will complete it, never claim it is done. An interview
   invitation: accept and confirm an online format and a time slot.
6. Money: never go below the salary floor from the profile preferences; when asked, propose
   a figure that fits both the profile and the vacancy's range. Any salary question ALWAYS
   gets needs_owner_decision = true: the candidate picks the anchor for each vacancy himself.
7. needs_owner_decision = true when the answer can close the application or depends on a
   personal decision: "Нет" to a condition from the vacancy, relocation, office, night
   shifts, spoken English, money below the floor, a long test task, or a fact you cannot
   confirm from the profile. Explain why in "decision_note" — one sentence in Russian.
8. If nothing needs to be written (a template like "мы рассмотрим"), reply = "" and say so
   in decision_note.

Output ONLY a JSON object, no commentary:
{"robot": [{"question": "...", "button": "...", "reason": "..."}],
 "reply": "...", "needs_owner_decision": false, "decision_note": ""}"""


@dataclass
class RobotAnswer:
    question: str
    button: Optional[str]        # None — модель не выбрала настоящую кнопку
    reason: str


@dataclass
class ReplyDraft:
    robot: list = field(default_factory=list)
    reply: str = ""
    needs_decision: bool = False
    note: str = ""
    provider: str = ""


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "…"


def _who(m: dict, me: str) -> str:
    if str(m.get("participantId")) == me:
        return "Я (кандидат)"
    name = (m.get("participantDisplay") or {}).get("name") or "?"
    if is_hh(m):
        return "hh.ru (служебное)"
    if is_robot(m):
        return f"{name} (робот-рекрутёр)"
    return f"{name} (работодатель)"


def chat_transcript(view: ChatView) -> str:
    lines = []
    first_mine = True
    for m in view.messages:
        t = flat(m.get("text"))
        mine = str(m.get("participantId")) == view.my_id
        if not t and m.get("type") != "PARTICIPANT_LEFT":
            continue
        if m.get("type") == "PARTICIPANT_LEFT":
            t = t or "покинул чат"
        cap = _MINE_CAP if mine and first_mine else _MSG_CAP
        if mine:
            first_mine = False
        btn = [b.get("text") for b in ((m.get("actions") or {}).get("text_buttons") or [])]
        line = f"[{samara_time(m.get('creationTime') or '')}] {_who(m, view.my_id)}: {_cut(t, cap)}"
        if btn:
            line += f"  [кнопки: {' | '.join(b for b in btn if b)}]"
        lines.append(line)
    text = "\n".join(lines)
    if len(text) > _CHAT_CAP and lines:
        # Первое сообщение (письмо) оставляем, середину выкидываем.
        head, tail = lines[0], []
        size = len(head)
        for ln in reversed(lines[1:]):
            if size + len(ln) > _CHAT_CAP:
                break
            tail.insert(0, ln)
            size += len(ln)
        text = "\n".join([head, "[… середина переписки пропущена …]", *tail])
    return text


def build_prompt(view: ChatView, vacancy_text: str, profile: str) -> str:
    waiting = []
    for q in view.robot:
        waiting.append(f'- Robot question: "{q.text}" | buttons: {" | ".join(q.buttons)} | '
                       f'robot left the chat: {"yes" if q.left else "no"}')
    for m in view.ask:
        kind = "interview invitation" if m.invite else "message"
        waiting.append(f'- {kind} from {m.author}: "{_cut(m.text, _MSG_CAP)}"')
    vac = _cut(vacancy_text.strip(), _VACANCY_CAP) if vacancy_text else "(text not available)"
    return (
        f"CANDIDATE PROFILE:\n{profile}\n\n===\n\n"
        f"VACANCY: {view.vacancy}\nEMPLOYER: {view.employer}\n{vac}\n\n===\n\n"
        f"CHAT (Samara time, oldest first):\n{chat_transcript(view)}\n\n===\n\n"
        "WAITING FOR THE CANDIDATE'S ANSWER:\n" + "\n".join(waiting) +
        "\n\nWrite the JSON now."
    )


def _parse_json(raw: str) -> Optional[dict]:
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start == -1 or end == 0:
        return None
    try:
        return json.loads(raw[start:end])
    except json.JSONDecodeError:
        try:
            # Как в скоринге: изредка модель вставляет в строку сырой перевод строки.
            return json.loads(raw[start:end], strict=False)
        except json.JSONDecodeError:
            return None


def match_button(choice: str, buttons: list) -> Optional[str]:
    """Кнопка из ответа модели → точный текст настоящей кнопки или None."""
    c = flat(choice).strip("«»\"'").lower()
    for b in buttons:
        if flat(b).lower() == c:
            return b
    return None


def draft_from_json(data: dict, view: ChatView) -> ReplyDraft:
    by_text = {q.text: q for q in view.robot}
    answers = []
    got = data.get("robot") if isinstance(data.get("robot"), list) else []
    for i, q in enumerate(view.robot):
        # Сопоставляем по тексту вопроса, а если модель его переписала — по порядку.
        item = next((a for a in got if isinstance(a, dict)
                     and flat(str(a.get("question", ""))) == q.text), None)
        if item is None and i < len(got) and isinstance(got[i], dict):
            item = got[i]
        item = item or {}
        answers.append(RobotAnswer(question=by_text[q.text].text,
                                   button=match_button(str(item.get("button", "")), q.buttons),
                                   reason=flat(str(item.get("reason", "")))))
    return ReplyDraft(robot=answers, reply=str(data.get("reply") or "").strip(),
                      needs_decision=bool(data.get("needs_owner_decision")),
                      note=flat(str(data.get("decision_note") or "")))


def generate_reply(view: ChatView, vacancy_text: str = "") -> Optional[ReplyDraft]:
    """Вариант ответа по чату. None — ни один провайдер не справился."""
    providers = available_providers()
    if not providers:
        logger.error("Чаты hh: не задан ни один ключ AI-провайдера")
        return None
    prompt = build_prompt(view, vacancy_text, _build_profile_text())
    for p in providers:
        try:
            resp = _get_client(p).chat.completions.create(
                model=p.model,
                messages=[{"role": "system", "content": _INSTRUCTION},
                          {"role": "user", "content": prompt}],
                temperature=0.3,
                # gpt-oss тратит часть выходных токенов на рассуждения (см.
                # generate_cover_letter): с малым запасом JSON обрывается.
                max_tokens=2500,
                timeout=_TIMEOUT,
            )
            data = _parse_json(resp.choices[0].message.content or "")
            if data is None:
                logger.warning("Чаты hh: %s вернул не JSON (чат %s)", p.name, view.chat_id)
                continue
            draft = draft_from_json(data, view)
            draft.provider = p.name
            return draft
        except Exception as e:
            logger.error("Чаты hh: %s не справился с ответом — %s", p.name, str(e)[:200])
    return None
