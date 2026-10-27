"""Ночные смены: отсев режима, штраф за редкие ночи и сменный график.

Повод — 19.08.2026: Александр прислал две вакансии, приехавшие в один прогон.
«Специалист поддержки» Контура («Рабочие часы: Вечерние или ночные смены»)
и «PSP Support Agent» SOFTSWISS («2/2 shift schedule (12-hour shifts,
including 2–4 night shifts per month)»). Фильтра смен не было вообще.

Решение владельца: ночь как РЕЖИМ — жёсткий отсев; считанные ночи в месяц и
сменный график — штраф, чтобы сильная вакансия всё же дошла.

26.10.2026 решение ужесточено: цель — свободный график под тренировки, поэтому
смены как ЕДИНСТВЕННЫЙ режим (2/2, 6/1, 12-часовые, ночные по полю hh) режутся
(`_rigid_schedule`), а смешанный график («5/2 или 2/2 на выбор») — по-прежнему
штраф. За 22.09–26.10 таких ушло в Telegram 41 из 888, 24 — только по полям hh.
"""
import pytest

from src.matcher import pre_filter as pf
from src.matcher.pre_filter import _night_shift_mode, score_vacancy
from src.parsers import hh_enrich as he


class TestCoreNight:
    """Ночь как штатный режим — жёсткий отсев."""

    @pytest.mark.parametrize("txt", [
        "Рабочие часы: Вечерние или ночные смены, 8",      # Контур, дословно
        "График работы: ночные смены",
        "Работа в ночное время суток",
        "Требуется работа в ночную смену",
        "Night shift support engineer",
        "You will work overnight shifts",
        "graveyard shift rotation",
        "график сутки через двое",
        "ночной график работы",
    ])
    def test_core_is_rejected(self, txt):
        assert _night_shift_mode(txt.lower()) == "core"


class TestOccasionalNight:
    """Считанные ночи в месяц — штраф, не отсев."""

    @pytest.mark.parametrize("txt", [
        # SOFTSWISS, дословно — главный случай ради которого нужна развилка
        "Work on a 2/2 shift schedule (12-hour shifts, including 2-4 night shifts per month)",
        "including 2–4 night shifts per month",
        "1 night shift per month",
        "2-3 ночных смены в месяц",
        "occasional night shifts",
        "иногда ночные смены",
    ])
    def test_occasional_is_penalty_not_reject(self, txt):
        assert _night_shift_mode(txt.lower()) == "occasional", \
            "редкие ночи не должны попадать под жёсткий отсев"


class TestShiftSchedule:
    """Сменный график без ночей — меньший штраф."""

    @pytest.mark.parametrize("txt", [
        "сменный график работы",
        "посменная работа",
        "rotating shifts",
        "shift schedule",
        "график 2/2",
        "работа по графику 3/3",
    ])
    def test_shift_detected(self, txt):
        assert _night_shift_mode(txt.lower()) == "shift"


class TestNoFalsePositives:
    """Главный риск: перелов. Эти формулировки трогать нельзя."""

    @pytest.mark.parametrize("txt", [
        # «5/2» — обычная пятидневка. Штраф за неё срезал бы половину рынка.
        "график работы 5/2",
        "полный день, график 5/2, офис",
        "5/2 schedule, standard business hours",
        # Ночь не про график
        "мониторинг транзакций в режиме 24/7 силами дежурной команды",
        "overnight delivery of reports to the client",
        "ночной клуб — наш клиент",
        # Смена не про график
        "смена парадигмы в подходе к поддержке",
        "смена пароля пользователя",
        "shift the focus to automation",
        # Обычная вакансия
        "Специалист поддержки, удалённо, полный день",
        "Customer Support Specialist, remote, full-time",
    ])
    def test_clean_text_is_untouched(self, txt):
        assert _night_shift_mode(txt.lower()) is None, \
            f"ложное срабатывание на: {txt}"


class TestOrderMatters:
    def test_occasional_wins_over_core_in_same_text(self):
        # «night shifts» есть в обеих формулировках; квалификатор «per month»
        # должен победить, иначе SOFTSWISS уедет в жёсткий отсев.
        txt = ("work on a 2/2 shift schedule (12-hour shifts, "
               "including 2-4 night shifts per month)")
        assert _night_shift_mode(txt) == "occasional"


class TestScheduleNote:
    """Пометка для AI: без неё модель про смены не знает вообще.

    Замер 19.08.2026 на вакансии SOFTSWISS: фраза «2–4 night shifts per month»
    стояла на позиции 2183 из 3245 символов, а в промпт уходят первые 700 и
    последние 800 — середина выбрасывается. Балл падал 72→58 только за счёт
    профиля и инструкции, про смены модель молчала. С пометкой — 35 и явное
    «указаны ночные смены» в watch_out.
    """

    def test_core_note(self):
        from src.parsers.normalize import schedule_note
        note = schedule_note("Рабочие часы: вечерние или ночные смены")
        assert "NIGHT SHIFTS" in note and "regular working pattern" in note

    def test_occasional_note(self):
        from src.parsers.normalize import schedule_note
        note = schedule_note("2/2 schedule including 2-4 night shifts per month")
        assert "occasional NIGHT SHIFTS" in note

    def test_shift_note_says_no_nights(self):
        # Важно: сменный график без ночей кандидат допускает — пометка не должна
        # выглядеть как ночная, иначе модель зарубит нормальные вакансии.
        from src.parsers.normalize import schedule_note
        note = schedule_note("сменный график работы 2/2")
        assert "no night shifts stated" in note

    def test_clean_description_gets_no_note(self):
        from src.parsers.normalize import schedule_note
        assert schedule_note("Полный день, удалённо, график 5/2") == ""
        assert schedule_note("") == ""
        assert schedule_note(None) == ""

    def test_note_reaches_prompt_even_when_middle_is_cut(self):
        # Регрессия на первопричину: фраза в выброшенной середине.
        from src.models import _sample_description
        from src.parsers.normalize import schedule_note
        desc = ("A" * 900) + " including 2-4 night shifts per month " + ("B" * 900)
        assert "night" not in _sample_description(desc).lower(), "тест потерял смысл"
        assert "NIGHT SHIFTS" in schedule_note(desc)


_SUPPORT = "Поддержка клиентов в чате. Обязанности: ответы клиентам, CRM.\n"


def _hh_page(days=None, hours=None, night=None, formats=("REMOTE",)) -> bytes:
    """Поля страницы hh: JSON в атрибуте, кавычки — &#34;."""
    def arr(codes):
        return b"[" + b",".join(b"&#34;" + c.encode() + b"&#34;" for c in codes) + b"]"
    parts = [b"&#34;workFormats&#34;:" + arr(formats)]
    if days is not None:
        parts.append(b"&#34;workScheduleByDays&#34;:" + arr(days))
    if hours is not None:
        parts.append(b"&#34;workingHours&#34;:" + arr(hours))
    if night is not None:
        parts.append(b"&#34;nightShifts&#34;:" + (b"true" if night else b"false"))
    return b'<div data-state="{' + b",".join(parts) + b'}"></div>'


def _hh_text(days=None, hours=None, night=None, text=_SUPPORT) -> str:
    """Описание hh в том порядке, в каком его собирает hh_enrich._fetch."""
    page = _hh_page(days, hours, night)
    return he._schedule_note(page) + he._format_note(page) + text


def _hh_blob(days=None, hours=None, night=None, text=_SUPPORT) -> str:
    return pf._n(_hh_text(days, hours, night, text))


class TestRigidScheduleByHhFields:
    """Смены как РЕЖИМ по полям hh — жёсткий отсев. Штраф −8 не держал: AI ставил
    таким 80+, а в тексте описания смен часто нет вовсе."""

    @pytest.mark.parametrize("days,hours,night,reason", [
        (["TWO_ON_TWO_OFF"], None, None, "график hh 2/2"),
        # OsteoFace, hh 137767979: 6/1 только в поле, часы «8 или по договорённости»
        (["SIX_ON_ONE_OFF"], ["HOURS_8", "FLEXIBLE"], False, "график hh 6/1"),
        (["WEEKEND"], None, None, "график hh по выходным"),
        (["TWO_ON_TWO_OFF", "THREE_ON_THREE_OFF"], None, None, "график hh 2/2, 3/3"),
        (["FIVE_ON_TWO_OFF"], ["HOURS_12"], None, "смена hh 12 ч"),
        (None, ["HOURS_24"], None, "смена hh 24 ч"),
        (["FIVE_ON_TWO_OFF"], ["HOURS_8"], True, "ночные смены по полю hh"),
    ])
    def test_cut(self, days, hours, night, reason):
        assert pf._rigid_schedule(_hh_blob(days, hours, night)) == reason

    @pytest.mark.parametrize("days,hours,night", [
        # «5/2 или 2/2 на выбор» — спорное, решение владельца: не трогать
        (["FIVE_ON_TWO_OFF", "TWO_ON_TWO_OFF"], None, None),
        (["FIVE_ON_TWO_OFF"], ["HOURS_12", "FLEXIBLE"], None),   # 12 ч или по договорённости
        (["FLEXIBLE"], ["HOURS_8"], True),                       # ночи есть, но график свободный
        (["FIVE_ON_TWO_OFF"], ["FLEXIBLE"], True),
        # ночь — одна из опций при выборе 5/2 (замер 27.10: 101 Интернет «2/2 или 5/2,
        # смены по 6, 9 или 12 часов, доплата 50% за ночь»; «5/2, 2/2 в день, 2/2 в ночь»)
        (["FIVE_ON_TWO_OFF", "TWO_ON_TWO_OFF"], ["HOURS_6", "HOURS_9", "HOURS_12"], True),
        (["FIVE_ON_TWO_OFF", "TWO_ON_TWO_OFF"], ["HOURS_8", "HOURS_10"], True),
        (["FIVE_ON_TWO_OFF"], ["HOURS_8"], False),
        (["OTHER"], ["HOURS_8"], False),
    ])
    def test_not_cut(self, days, hours, night):
        assert pf._rigid_schedule(_hh_blob(days, hours, night)) is None

    def test_restik_other_keeps_penalty(self):
        # Restik (hh 138178885): «сменный график с 11:00 до 19:00», в поле — «другой».
        # Это дневная работа: не режем, остаётся штраф −8.
        blob = _hh_blob(["OTHER"], ["HOURS_8"], False,
                        "Что тебя ждет в Restik: удаленная работа, сменный график "
                        "с 11:00 до 19:00 по МСК.")
        assert pf._rigid_schedule(blob) is None
        assert pf._night_shift_mode(blob) == "shift"

    def test_hh_codes_do_not_look_like_phrases(self):
        # Строки hh пишутся кодами: подпись «график (hh): 2/2» поймал бы
        # _SHIFT_SCHEDULE, а «ночные смены (hh)» — _NIGHT_CORE без поправки на
        # свободный график, и смешанные вакансии ушли бы в отсев.
        blob = _hh_blob(["TWO_ON_TWO_OFF", "SIX_ON_ONE_OFF"], ["HOURS_12", "HOURS_24"],
                        True, "")
        assert pf._night_shift_mode(blob) is None


class TestRigidScheduleByText:
    """Без полей hh (LinkedIn, ATS, Telegram) — по фразе в тексте: смены без
    отрицания и без альтернативы (5/2, пятидневка, гибкий график, flexible)."""

    @pytest.mark.parametrize("txt,reason", [
        ("Support agent. Rotating shifts, 24/7 coverage.", "«rotating shifts»"),
        ("Сменный график работы", "«сменный график»"),
        ("График работы: 2/2, дневные смены с 8 до 20", "«график работы: 2/2»"),
        ("Shift work, weekends included", "«shift work»"),
        ("Посменная работа в контакт-центре", "«посменн»"),
        # отрицание стоит у «ночной», а не у смен
        ("Сменный график, но не ночной", "«сменный график»"),
    ])
    def test_cut(self, txt, reason):
        assert pf._rigid_schedule(pf._n(txt)) == reason

    @pytest.mark.parametrize("txt", [
        "Rotating shifts or flexible schedule",
        "График 5/2 или сменный график 2/2 на выбор",
        "Сменный график, гибкое начало рабочего дня",
        "Работа без сменного графика, с 10 до 19",
        "Сменного графика нет",
        "Сменный график не предусмотрен",
        "No shift work, Monday to Friday",
        "График работы 5/2",
        "Специалист поддержки, удалённо, полный день",
        # диапазон будних дней — пятидневка, а не смены (27.10.2026)
        "Shift schedule: Monday-Friday 9am-6pm",
        "Shift schedule: Mon - Fri",
        "Shift work: Sunday-Thursday",            # рабочая неделя ОАЭ
        "Сменная работа с понедельника по пятницу",
        "Сменный график, пн-пт",
    ])
    def test_not_cut(self, txt):
        assert pf._rigid_schedule(pf._n(txt)) is None

    @pytest.mark.parametrize("txt", [
        "Rotating shifts, Saturday-Wednesday",
        "Shift schedule: monthly rotation, nights",
    ])
    def test_other_day_ranges_still_cut(self, txt):
        assert pf._rigid_schedule(pf._n(txt)) is not None


class TestShiftNegation:
    """«Без сменного графика» — плюс вакансии. До 26.10.2026 отрицание давало −8,
    как сами смены (то же было с ночными до 17.09.2026)."""

    @pytest.mark.parametrize("txt", [
        "работа без сменного графика",
        "работа непосменная, 5 дней",     # «не» вплотную: _SHIFT_SCHEDULE ловит «посменн»
        "сменного графика нет",
        "сменный график не предусмотрен",
        "no shift work, weekdays only",
    ])
    def test_negated_is_not_shift(self, txt):
        assert _night_shift_mode(txt) is None

    def test_negation_of_something_else_does_not_count(self):
        assert _night_shift_mode("сменный график, но не ночной") == "shift"


class TestScheduleInScoring:
    """Через score_vacancy: смены-режим режут вакансию, смешанный график — штраф."""

    _TEXT = ("Менеджер по работе с клиентами в SaaS. Удержание действующих клиентов, "
             "CRM.\n")

    def test_rigid_schedule_is_hard_reject(self):
        r = score_vacancy("Аккаунт-менеджер",
                          _hh_text(["SIX_ON_ONE_OFF"], ["HOURS_8", "FLEXIBLE"], False,
                                   self._TEXT), "sales_remote")
        assert not r["passed_gate"]
        assert r["reasons"] == ["сменный график как режим: график hh 6/1"]

    def test_mixed_schedule_keeps_penalty(self):
        text = self._TEXT + "Сменный график 2/2 или 5/2 на выбор.\n"
        r = score_vacancy("Аккаунт-менеджер",
                          _hh_text(["FIVE_ON_TWO_OFF", "TWO_ON_TWO_OFF"], ["HOURS_8"], False,
                                   text), "sales_remote")
        assert r["passed_gate"]
        assert "-8 сменный график" in r["reasons"]
