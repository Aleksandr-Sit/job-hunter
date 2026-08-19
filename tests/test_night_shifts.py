"""Ночные смены: отсев режима, штраф за редкие ночи и сменный график.

Повод — 19.08.2026: Александр прислал две вакансии, приехавшие в один прогон.
«Специалист поддержки» Контура («Рабочие часы: Вечерние или ночные смены»)
и «PSP Support Agent» SOFTSWISS («2/2 shift schedule (12-hour shifts,
including 2–4 night shifts per month)»). Фильтра смен не было вообще.

Решение владельца: ночь как РЕЖИМ — жёсткий отсев; считанные ночи в месяц и
сменный график — штраф, чтобы сильная вакансия всё же дошла.
"""
import pytest

from src.matcher.pre_filter import _night_shift_mode


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
