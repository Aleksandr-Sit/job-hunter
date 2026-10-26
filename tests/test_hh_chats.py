"""Сторож чатов hh (этап 1): разбор переписки, cookie, алерты, «каждую позицию — один раз».

Всё без сети: ответы chatik подменены словарями той же формы, что отдаёт живой
сервис (поля сверены 26.10.2026), Telegram и языковая модель — заглушками.
"""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from src import storage
from src.hh_chats import client as cl
from src.hh_chats import watch
from src.hh_chats.reply import ReplyDraft, RobotAnswer, draft_from_json, match_button
from src.hh_chats.sweep import analyze_chat, in_window, samara_time, since_date

ME = "24197479"
ROOT = Path(__file__).resolve().parent.parent

_PEOPLE = {
    "me": (ME, {"name": "Александр", "isBot": False}),
    "robot": ("900", {"name": "Робот-рекрутёр", "isBot": True}),
    "hr": ("500", {"name": "Анна", "isBot": False}),
    "hh": ("1", {"name": "hh.ru", "isBot": True}),
}


def ts(days_ago: float = 0, minutes: int = 0) -> str:
    dt = datetime.now(timezone(timedelta(hours=3))) - timedelta(days=days_ago) + \
        timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+03:00")


def msg(mid, when, who, text="", buttons=None, state=None, mtype="SIMPLE"):
    pid, disp = _PEOPLE[who]
    m = {"id": mid, "chatId": 111, "creationTime": when, "text": text, "type": mtype,
         "participantId": pid, "participantDisplay": disp}
    if buttons:
        m["actions"] = {"text_buttons": [{"size": "S", "text": b} for b in buttons]}
    if state:
        m["workflowTransition"] = {"id": 1, "topicId": 2, "applicantState": state,
                                   "declinedByApplicant": False}
    return m


def chat(cid, messages, write=True, vacancy="555", title="Специалист поддержки",
         employer="ООО Ромашка"):
    item = {"id": cid, "type": "NEGOTIATION", "currentParticipantId": ME,
            "resources": {"VACANCY": [vacancy], "NEGOTIATION_TOPIC": ["7"]},
            "lastMessage": {"creationTime": max(m["creationTime"] for m in messages)}}
    data = {"chat": {"id": cid, "currentParticipantId": ME,
                     "messages": {"items": list(messages), "hasMore": False}},
            "chatStates": {"writeMessageState": {"allowed": write}}}
    return item, {str(cid): {"title": title, "subtitle": employer, "icon": ""}}, data


SINCE = since_date(30)


def view_of(messages, **kw):
    item, disp, data = chat(111, messages, **kw)
    return analyze_chat(item, disp, data, SINCE)


# ── разбор переписки ──────────────────────────────────────────────────────────

class TestAnalyze:
    def test_robot_question_grouped_and_alive(self):
        v = view_of([
            msg(1, ts(3), "me", "Здравствуйте! Моё письмо"),
            msg(2, ts(2), "robot", "Есть ли опыт от 2 лет?", ["от 2 лет", "до 2 лет"]),
            msg(3, ts(1), "robot", "Есть ли опыт от 2 лет?", ["от 2 лет", "до 2 лет"]),
        ])
        assert len(v.robot) == 1
        q = v.robot[0]
        assert q.repeats == 2 and q.buttons == ["от 2 лет", "до 2 лет"] and not q.left
        assert q.key.startswith("robot:111:")
        assert v.ask == [] and v.news == []

    def test_robot_left_marks_question(self):
        v = view_of([
            msg(1, ts(5), "me", "письмо"),
            msg(2, ts(4), "robot", "Готовы к релокации?", ["Да", "Нет"]),
            msg(3, ts(2), "robot", "Робот-рекрутёр покинул чат", mtype="PARTICIPANT_LEFT"),
        ])
        assert v.robot[0].left is True

    def test_answered_robot_is_not_waiting(self):
        v = view_of([
            msg(1, ts(5), "me", "письмо"),
            msg(2, ts(4), "robot", "Готовы к релокации?", ["Да", "Нет"]),
            msg(3, ts(3), "me", "Да"),
        ])
        assert v.robot == [] and v.ask == []

    def test_human_question_link_invite_go_to_ask(self):
        v = view_of([
            msg(1, ts(5), "me", "письмо"),
            msg(2, ts(3), "hr", "Какие ожидания по зарплате?"),
            msg(3, ts(2), "hr", "Пройдите тест https://forms.example/x"),
            msg(4, ts(1), "hr", "Приглашаем на интервью", state="INTERVIEW"),
            msg(5, ts(1, 5), "hr", "Спасибо за отклик, мы рассмотрим резюме"),
        ])
        assert [m.id for m in v.ask] == [2, 3, 4]
        assert v.ask[2].invite is True
        assert [m.id for m in v.news] == [5]
        assert v.ask[0].key == "msg:2"

    def test_hh_noise_filtered_warning_kept(self):
        v = view_of([
            msg(1, ts(5), "me", "письмо"),
            msg(2, ts(4), "hh", "Работодатель подготовил несколько вопросов"),
            msg(3, ts(3), "hh", "Вакансия не прошла проверку и была удалена"),
        ])
        assert [m.id for m in v.hh_warn] == [3]
        assert v.ask == [] and v.news == []

    def test_discard_closes_chat(self):
        v = view_of([
            msg(1, ts(5), "me", "письмо"),
            msg(2, ts(4), "hr", "Есть вопрос?"),
            msg(3, ts(2), "hr", "К сожалению, отказ", state="DISCARD"),
        ])
        assert v.discard_time and v.ask == [] and v.robot == []

    def test_invite_after_discard_reopens(self):
        v = view_of([
            msg(1, ts(6), "me", "письмо"),
            msg(2, ts(5), "hr", "Отказ", state="DISCARD"),
            msg(3, ts(1), "hr", "Передумали, приглашаем", state="INTERVIEW"),
        ])
        assert not v.discard_time and v.ask[0].invite

    def test_old_human_message_outside_window(self):
        v = view_of([
            msg(1, ts(60), "me", "письмо"),
            msg(2, ts(45), "hr", "Вы ещё ищете работу?"),
        ])
        assert v.ask == []

    def test_window_and_type_filter(self):
        item, _, _ = chat(1, [msg(1, ts(1), "me", "x")])
        assert in_window(item, SINCE)
        assert not in_window({**item, "type": "SUPPORT"}, SINCE)
        old, _, _ = chat(2, [msg(1, ts(40), "me", "x")])
        assert not in_window(old, SINCE)

    def test_write_disabled_and_links(self):
        v = view_of([msg(1, ts(1), "me", "x")], write=False)
        assert v.write_allowed is False
        assert v.chat_url == "https://hh.ru/chat/111"
        assert v.vacancy_url == "https://hh.ru/vacancy/555"

    def test_samara_time(self):
        # API отдаёт МСК, показываем по Самаре (+1 час)
        assert samara_time("2026-10-26T14:05:00.000+03:00") == "26.10 15:05"


# ── cookie ────────────────────────────────────────────────────────────────────

NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    "# comment\n"
    "#HttpOnly_.hh.ru\tTRUE\t/\tTRUE\t0\thhtoken\tSECRET_TOKEN_VALUE\n"
    ".hh.ru\tTRUE\t/\tTRUE\t0\t_xsrf\tXSRF_VALUE\n"
    "samara.hh.ru\tFALSE\t/\tTRUE\t0\tregion_only\tR\n"
    ".google.com\tTRUE\t/\tTRUE\t0\tNID\tFOREIGN\n"
)


class TestCookies:
    def test_netscape_with_httponly_and_foreign_filtered(self):
        cs = cl.parse_cookies(NETSCAPE)
        assert {c["name"] for c in cs} == {"hhtoken", "_xsrf", "region_only"}

    def test_json_formats(self):
        lst = '[{"domain": ".hh.ru", "name": "hhtoken", "value": "v", "path": "/"}]'
        assert [c["name"] for c in cl.parse_cookies(lst)] == ["hhtoken"]
        wrapped = '{"cookies": [{"domain": "chatik.hh.ru", "name": "a", "value": "b"}]}'
        assert [c["name"] for c in cl.parse_cookies(wrapped)] == ["a"]

    def test_header_string(self):
        cs = cl.parse_cookies("Cookie: hhtoken=abc; _xsrf=def")
        assert [(c["name"], c["domain"]) for c in cs] == [("hhtoken", ".hh.ru"),
                                                          ("_xsrf", ".hh.ru")]

    def test_missing_file_is_session_problem(self, tmp_path):
        with pytest.raises(cl.SessionExpired):
            cl.load_cookies(tmp_path / "nope")
        (tmp_path / "empty").mkdir()
        with pytest.raises(cl.SessionExpired):
            cl.load_cookies(tmp_path / "empty")

    def test_all_expired_is_session_problem(self, tmp_path):
        (tmp_path / "c.txt").write_text(".hh.ru\tTRUE\t/\tTRUE\t1000\thhtoken\tv\n")
        with pytest.raises(cl.SessionExpired):
            cl.load_cookies(tmp_path)

    def test_newer_export_overrides(self, tmp_path):
        import os
        old, new = tmp_path / "a.txt", tmp_path / "b.json"
        old.write_text(".hh.ru\tTRUE\t/\tTRUE\t0\thhtoken\tOLD\n")
        new.write_text('[{"domain": ".hh.ru", "name": "hhtoken", "value": "NEW", "path": "/"}]')
        os.utime(old, (1_000_000, 1_000_000))
        cs = cl.load_cookies(tmp_path)
        assert [c["value"] for c in cs] == ["NEW"]

    def test_values_never_logged(self, tmp_path, caplog):
        (tmp_path / "c.txt").write_text(NETSCAPE)
        with caplog.at_level(logging.INFO):
            cl.load_cookies(tmp_path)
        assert "hhtoken" in caplog.text           # имена — можно
        assert "SECRET_TOKEN_VALUE" not in caplog.text
        assert "XSRF_VALUE" not in caplog.text

    def test_session_sends_hh_cookies_to_chatik_only(self):
        s = cl.build_session(cl.parse_cookies(NETSCAPE))
        req = s.prepare_request(requests.Request("GET", cl.API + "chats"))
        cookie = req.headers.get("Cookie", "")
        assert "hhtoken=" in cookie and "_xsrf=" in cookie
        assert "region_only" not in cookie        # cookie samara.hh.ru чату не нужен


# ── клиент chatik ─────────────────────────────────────────────────────────────

class FakeResp:
    def __init__(self, status=200, body=None, ctype="application/json", set_cookies=()):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = {"Content-Type": ctype}
        self.cookies = [type("C", (), {"name": n})() for n in set_cookies]

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None, allow_redirects=True):
        self.calls.append((url, dict(params or {})))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client_with(*responses):
    return cl.ChatikClient(FakeSession(responses), pause=0)


class TestClient:
    @pytest.mark.parametrize("resp", [
        FakeResp(403, {"enable_dark_theme": False}),   # так chatik отвечает без входа
        FakeResp(401),
        FakeResp(302, ctype="text/html"),
        FakeResp(200, "<html>login</html>", ctype="text/html; charset=utf-8"),
    ])
    def test_session_expired(self, resp):
        with pytest.raises(cl.SessionExpired):
            client_with(resp).list_chats()

    @pytest.mark.parametrize("resp", [
        FakeResp(502), FakeResp(429), requests.Timeout("slow"),
        FakeResp(200, {"unexpected": 1}),
    ])
    def test_network_or_format_problem(self, resp):
        with pytest.raises(cl.ChatikUnavailable):
            client_with(resp).list_chats()

    def test_pagination_and_rotated_cookie_names(self):
        p1 = {"chats": {"items": [{"id": 1}, {"id": 2}], "nextFrom": "CURSOR"},
              "chatsDisplayInfo": {"1": {"title": "a"}}}
        p2 = {"chats": {"items": [{"id": 3}], "nextFrom": None},
              "chatsDisplayInfo": {"3": {"title": "c"}}}
        c = client_with(FakeResp(200, p1, set_cookies=["_xsrf"]), FakeResp(200, p2))
        chats, disp = c.list_chats()
        assert [x["id"] for x in chats] == [1, 2, 3]
        assert set(disp) == {"1", "3"}
        assert c.session.calls[1][1] == {"perPage": 20, "from": "CURSOR"}
        assert c.rotated == {"_xsrf"}


# ── вариант ответа ────────────────────────────────────────────────────────────

class TestReplyParsing:
    def test_match_button(self):
        assert match_button("до 2 лет", ["от 2 лет", "до 2 лет"]) == "до 2 лет"
        assert match_button(" «ДА» ", ["Да", "Нет"]) == "Да"
        assert match_button("Скорее да", ["Да", "Нет"]) is None

    def test_draft_maps_by_text_then_order_and_rejects_fake_button(self):
        v = view_of([
            msg(1, ts(3), "me", "письмо"),
            msg(2, ts(2), "robot", "Вопрос один?", ["Да", "Нет"]),
            msg(3, ts(1), "robot", "Вопрос два?", ["от 2 лет", "до 2 лет"]),
        ])
        d = draft_from_json({"robot": [
            {"question": "переписанный вопрос", "button": "Да", "reason": "r1"},
            {"question": "Вопрос два?", "button": "больше 5 лет", "reason": "r2"}],
            "reply": " ", "needs_owner_decision": True, "decision_note": "закрывает"}, v)
        assert d.robot[0].button == "Да"
        assert d.robot[1].button is None          # такой кнопки нет — решает владелец
        assert d.needs_decision and d.reply == ""


# ── карточка ──────────────────────────────────────────────────────────────────

class TestCard:
    def _view(self):
        return view_of([
            msg(1, ts(3), "me", "письмо"),
            msg(2, ts(2), "robot", "Опыт <b>от</b> 2 лет?", ["от 2 лет", "до 2 лет"]),
            msg(3, ts(1), "hr", "Когда удобно созвониться? A&B"),
        ], employer="ООО <Ромашка>")

    def test_card_content_and_escaping(self):
        v = self._view()
        d = ReplyDraft(robot=[RobotAnswer(v.robot[0].text, "до 2 лет", "в письме так")],
                       reply="Удобно завтра <после 11>", needs_decision=True,
                       note="ответ может закрыть отклик")
        text = watch.format_card(v, d)
        assert "ООО &lt;Ромашка&gt;" in text
        assert "Опыт &lt;b&gt;от&lt;/b&gt; 2 лет?" in text
        assert "<code>до 2 лет</code>" in text
        assert "<pre>Удобно завтра &lt;после 11&gt;</pre>" in text
        assert "Решать тебе" in text
        assert 'href="https://hh.ru/vacancy/555"' in text
        assert dict(watch.card_links(v))["💬 Открыть чат на hh"] == "https://hh.ru/chat/111"

    def test_card_without_ai(self):
        assert "ответь сам" in watch.format_card(self._view(), None)

    def test_card_fits_telegram(self):
        v = view_of([msg(1, ts(3), "me", "x")] +
                    [msg(10 + i, ts(1, i), "hr", "Вопрос? " + "очень длинно " * 400)
                     for i in range(4)])
        assert len(watch.format_card(v, ReplyDraft(reply="ok"))) <= 4000

    def test_demo_card_builds(self):
        text, links = watch.demo_card()
        assert "ТЕСТ" in text and links[0][1].startswith("https://hh.ru/chat/")


# ── запуск: один раз, алерты ──────────────────────────────────────────────────

class FakeClient:
    def __init__(self, chats=(), error=None):
        self.chats = {c[0]["id"]: c for c in chats}
        self.error = error
        self.rotated = set()

    def list_chats(self):
        if self.error:
            raise self.error
        disp = {}
        for _, d, _ in self.chats.values():
            disp.update(d)
        return [c[0] for c in self.chats.values()], disp

    def chat_data(self, cid):
        return self.chats[cid][2]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")
    storage.init_db()
    return storage


class Outbox:
    def __init__(self):
        self.texts, self.cards = [], []

    def send(self, text):
        self.texts.append(text)
        return True

    def card(self, text, links):
        self.cards.append((text, links))
        return True


def run(client, out, **kw):
    return watch.run_watch({"since_days": 30, "max_cards": 10}, client=client,
                           send=out.send, send_card=out.card,
                           reply_fn=lambda v, t: ReplyDraft(reply="Добрый день!"),
                           vacancy_fn=lambda v: "", **kw)


def robot_chat(cid=111):
    return chat(cid, [msg(1, ts(3), "me", "письмо"),
                      msg(2, ts(1), "robot", "Готовы к релокации?", ["Да", "Нет"])])


class TestRun:
    def test_card_once_then_silence(self, db):
        out = Outbox()
        fc = FakeClient([robot_chat()])
        assert run(fc, out)["cards"] == 1
        assert run(fc, out)["cards"] == 0           # тот же вопрос — второй раз молчим
        assert len(out.cards) == 1 and out.texts == []

    def test_robot_repeat_does_not_resend(self, db):
        out = Outbox()
        item, disp, data = robot_chat()
        run(FakeClient([(item, disp, data)]), out)
        data["chat"]["messages"]["items"].append(
            msg(3, ts(0), "robot", "Готовы к релокации?", ["Да", "Нет"]))
        run(FakeClient([(item, disp, data)]), out)
        assert len(out.cards) == 1

    def test_new_human_question_sends_new_card(self, db):
        out = Outbox()
        item, disp, data = robot_chat()
        run(FakeClient([(item, disp, data)]), out)
        data["chat"]["messages"]["items"].append(msg(9, ts(0), "hr", "Когда созвонимся?"))
        run(FakeClient([(item, disp, data)]), out)
        assert len(out.cards) == 2

    def test_digest_for_news_and_discards(self, db):
        out = Outbox()
        news = chat(222, [msg(1, ts(3), "me", "письмо"),
                          msg(2, ts(1), "hr", "Спасибо, рассмотрим")], employer="Альфа")
        disc = chat(333, [msg(1, ts(3), "me", "письмо"),
                          msg(2, ts(1), "hr", "Отказ", state="DISCARD")], employer="Бета")
        run(FakeClient([news, disc]), out)
        assert out.cards == [] and len(out.texts) == 1
        assert "Альфа" in out.texts[0] and "Отказов за 30 дн.: 1" in out.texts[0]
        assert "Бета" not in out.texts[0]          # отказы — только счётчиком
        run(FakeClient([news, disc]), out)
        assert len(out.texts) == 1                 # ничего нового — тишина

    def test_nothing_new_is_silent(self, db):
        out = Outbox()
        quiet = chat(444, [msg(1, ts(2), "hr", "Есть вопрос?"), msg(2, ts(1), "me", "Ответ")])
        assert run(FakeClient([quiet]), out)["cards"] == 0
        assert out.texts == [] and out.cards == []

    def test_failed_send_is_retried_next_run(self, db):
        out = Outbox()
        fc = FakeClient([robot_chat()])
        watch.run_watch({"since_days": 30, "max_cards": 10}, client=fc, send=out.send,
                        send_card=lambda t, lk: False, reply_fn=lambda v, t: None,
                        vacancy_fn=lambda v: "")
        assert run(fc, out)["cards"] == 1          # не доставленная — придёт снова

    def test_session_alert_once_and_recovery(self, db):
        out = Outbox()
        bad = FakeClient(error=cl.SessionExpired("HTTP 403"))
        assert run(bad, out)["status"] == "session"
        run(bad, out)
        assert len(out.texts) == 1 and "Сессия hh истекла" in out.texts[0]
        run(FakeClient([]), out)
        assert len(out.texts) == 2 and "снова работает" in out.texts[1]
        run(FakeClient([]), out)
        assert len(out.texts) == 2

    def test_network_alert_only_after_two_fails(self, db):
        out = Outbox()
        down = FakeClient(error=cl.ChatikUnavailable("HTTP 502"))
        run(down, out)
        assert out.texts == []                     # одна неудача — не тревога
        run(down, out)
        assert len(out.texts) == 1 and "не отвечает" in out.texts[0]
        run(down, out)
        assert len(out.texts) == 1

    def test_missing_cookie_file_alerts_once(self, db, tmp_path):
        out = Outbox()
        for _ in range(2):
            watch.run_watch({"since_days": 30, "max_cards": 10},
                            cookie_path=str(tmp_path / "none"), send=out.send,
                            send_card=out.card)
        assert len(out.texts) == 1 and "нет файла cookie" in out.texts[0]

    def test_dry_run_sends_and_marks_nothing(self, db, capsys):
        out = Outbox()
        fc = FakeClient([robot_chat()])
        run(fc, out, dry_run=True)
        assert out.cards == [] and out.texts == []
        assert "Готовы к релокации" in capsys.readouterr().out
        assert run(fc, out)["cards"] == 1          # после dry-run карточка всё ещё новая

    def test_card_cap_per_run(self, db):
        out = Outbox()
        chats = [robot_chat(cid) for cid in range(1000, 1005)]
        res = watch.run_watch({"since_days": 30, "max_cards": 2}, client=FakeClient(chats),
                              send=out.send, send_card=out.card,
                              reply_fn=lambda v, t: None, vacancy_fn=lambda v: "")
        assert res["cards"] == 2
        assert run(FakeClient(chats), out)["cards"] == 3   # остальное — в следующий раз


# ── защита секрета и расписание ───────────────────────────────────────────────

class TestConfig:
    def test_cookie_dir_never_in_git_or_image(self):
        assert "config/hh_session/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "config/hh_session/" in (ROOT / ".dockerignore").read_text(encoding="utf-8")

    def test_schedule_is_noon_and_six_samara(self):
        import yaml
        cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
        # 12:00 и 18:00 по Самаре (UTC+4) = 08:00 и 14:00 UTC
        assert cfg["hh_chats"]["cron"] == "0 8,14 * * *"
        assert cfg["hh_chats"]["enabled"] is True
