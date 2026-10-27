"""Дообогащение HH: извлечение, устойчивость к отказам и защита от тихой поломки.

Повод — замер 19.08.2026: RSS отдаёт медиану 125 символов, у вакансии Контура
это были четыре строки метаданных. Весь HH скорился по заголовку.

Главное, что здесь проверяется, — не «работает ли парсинг», а что происходит,
когда он ПЕРЕСТАНЕТ работать: HH сменит вёрстку или начнёт отдавать 403.
Тихий отказ на этом проекте уже стоил суток простоя, повторять нельзя.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.models import Job
from src.parsers import hh_enrich as he

FULL = "Обязанности: " + "поддержка пользователей продукта. " * 20   # >300 симв.


def _job(vid="135351397", desc="Регион: Екатеринбург", source="hh.ru"):
    return Job(id=f"hh_{vid}", title="Специалист поддержки", company="Контур",
               description=desc, url=f"https://hh.ru/vacancy/{vid}", source=source)


def _resp(status=200, html=None):
    r = MagicMock()
    r.status_code = status
    r.content = (html if html is not None
                 else f'<div data-qa="vacancy-description">{FULL}</div>').encode()
    return r


class TestExtract:
    def test_primary_selector(self):
        assert FULL[:40] in he._extract(_resp().content)

    def test_fallback_selector_used_when_primary_gone(self):
        # Если HH переименует data-qa, запасной селектор ещё может спасти.
        html = f'<div class="vacancy-description">{FULL}</div>'.encode()
        assert FULL[:40] in he._extract(html)

    def test_too_short_is_not_accepted(self):
        # Пустой блок-заглушка не должен считаться описанием: иначе поломка
        # выглядит как успех и health-check никогда не сработает.
        html = '<div data-qa="vacancy-description">Ищем специалиста</div>'.encode()
        assert he._extract(html) == ""

    def test_no_match_returns_empty(self):
        assert he._extract(b"<html><body>nothing here</body></html>") == ""


class TestFetch:
    def test_ok(self):
        s = MagicMock(); s.get.return_value = _resp()
        assert FULL[:40] in he.fetch_description("https://hh.ru/vacancy/123", s)

    @pytest.mark.parametrize("status", [403, 404, 429, 500])
    def test_bad_status_returns_empty_not_raises(self, status):
        # 403 — реальный сценарий: api.hh.ru уже режет этот VPS.
        s = MagicMock(); s.get.return_value = _resp(status=status)
        assert he.fetch_description("https://hh.ru/vacancy/123", s) == ""

    def test_network_error_is_swallowed(self):
        s = MagicMock(); s.get.side_effect = OSError("connection reset")
        assert he.fetch_description("https://hh.ru/vacancy/123", s) == ""

    def test_url_without_vacancy_id(self):
        assert he.fetch_description("https://hh.ru/search?text=x") == ""
        assert he.fetch_description("") == ""


class TestEnrich:
    def test_only_hh_jobs_are_touched(self):
        other = _job(source="linkedin.com")
        before = other.description
        with patch.object(he, "_fetch", return_value=(FULL, 800_000)), \
             patch.object(he.time, "sleep"):
            st = he.enrich([_job(), other])
        assert st.attempted == 1, "трогать не-HH вакансии нельзя"
        assert other.description == before

    def test_description_is_appended_not_replaced(self):
        # В выжимке лежат регион и зарплата, которых нет в теле описания.
        j = _job(desc="Регион: Екатеринбург")
        with patch.object(he, "_fetch", return_value=(FULL, 800_000)), \
             patch.object(he.time, "sleep"):
            he.enrich([j])
        assert "Екатеринбург" in j.description
        assert FULL[:40] in j.description

    def test_failure_leaves_job_usable(self):
        j = _job(); before = j.description
        with patch.object(he, "_fetch", return_value=("", 800_000)), \
             patch.object(he.time, "sleep"):
            st = he.enrich([j])
        assert j.description == before, "при отказе описание портить нельзя"
        assert st.failed == 1 and st.enriched == 0

    def test_limit_caps_downloads(self):
        jobs = [_job(vid=str(i)) for i in range(200)]
        with patch.object(he, "_fetch", return_value=(FULL, 800_000)) as f, \
             patch.object(he.time, "sleep"):
            he.enrich(jobs, limit=30)
        assert f.call_count == 30

    def test_empty_input(self):
        assert he.enrich([]).attempted == 0


class TestHealthCheck:
    """Защита от тихого отказа — ради этого класса всё и написано."""

    def test_all_failed_is_unhealthy(self):
        jobs = [_job(vid=str(i)) for i in range(10)]
        with patch.object(he, "_fetch", return_value=("", 800_000)), \
             patch.object(he.time, "sleep"):
            st = he.enrich(jobs)
        assert not st.healthy, "полный отказ обязан подниматься как авария"

    def test_mostly_failed_is_unhealthy(self):
        jobs = [_job(vid=str(i)) for i in range(10)]
        seq = [FULL] * 3 + [""] * 7
        with patch.object(he, "_fetch", side_effect=[(t, 800_000) for t in seq]), \
             patch.object(he.time, "sleep"):
            st = he.enrich(jobs)
        assert st.success_rate == pytest.approx(0.3)
        assert not st.healthy

    def test_mostly_ok_is_healthy(self):
        jobs = [_job(vid=str(i)) for i in range(10)]
        seq = [FULL] * 8 + [""] * 2
        with patch.object(he, "_fetch", side_effect=[(t, 800_000) for t in seq]), \
             patch.object(he.time, "sleep"):
            st = he.enrich(jobs)
        assert st.healthy

    def test_tiny_sample_is_not_alarmed(self):
        # На двух вакансиях доля ничего не значит — ложная тревога хуже молчания.
        with patch.object(he, "_fetch", return_value=("", 800_000)), \
             patch.object(he.time, "sleep"):
            st = he.enrich([_job(vid="1"), _job(vid="2")])
        assert st.healthy


class TestByteAccounting:
    """Статистика должна считать скачанное, а не извлечённое.

    Первая версия писала в лог «трафик 0.1 МБ», подставляя туда длину
    извлечённого текста. Реальный трафик — 48 МБ: разница в 500 раз, и по
    такому логу невозможно заметить, что стадия съедает канал.
    """

    def test_downloaded_counts_page_not_text(self):
        with patch.object(he, "_fetch", return_value=(FULL, 800_000)), \
             patch.object(he.time, "sleep"):
            st = he.enrich([_job(vid=str(i)) for i in range(3)])
        assert st.downloaded == 2_400_000
        assert st.chars == len(FULL) * 3

    def test_failed_page_still_counts_traffic(self):
        # Страница скачана и отброшена — канал она всё равно съела.
        with patch.object(he, "_fetch", return_value=("", 800_000)), \
             patch.object(he.time, "sleep"):
            st = he.enrich([_job(vid=str(i)) for i in range(2)])
        assert st.downloaded == 1_600_000
        assert st.chars == 0


def _fields(days=None, hours=None, night=None, formats=None) -> str:
    """Поля страницы hh так, как они лежат в живой странице: JSON в атрибуте,
    кавычки экранированы как `&#34;`."""
    def arr(codes):
        return "[" + ",".join(f"&#34;{c}&#34;" for c in codes) + "]"
    parts = []
    if days is not None:
        parts.append(f"&#34;workScheduleByDays&#34;:{arr(days)}")
    if hours is not None:
        parts.append(f"&#34;workingHours&#34;:{arr(hours)}")
    if night is not None:
        parts.append(f"&#34;nightShifts&#34;:{'true' if night else 'false'}")
    if formats is not None:
        parts.append(f"&#34;workFormats&#34;:{arr(formats)}")
    return '<div data-state="{' + ",".join(parts) + '}"></div>'


class TestScheduleFields:
    """График, часы и ночные смены — поля страницы hh (26.10.2026).

    OsteoFace (hh 137767979): «График: 6/1» стоял только в поле, в тексте — ни
    слова, и владелец откликнулся, не зная о шестидневке. Из 41 сменной вакансии,
    ушедшей в Telegram за 22.09–26.10, 24 были видны ТОЛЬКО в полях страницы.
    """

    def test_flat_fields(self):
        html = _fields(["SIX_ON_ONE_OFF"], ["HOURS_8", "FLEXIBLE"], False).encode()
        assert he._schedule_note(html) == ("График (hh): SIX_ON_ONE_OFF\n"
                                           "Часы в день (hh): HOURS_8, FLEXIBLE\n")

    def test_nested_element_form(self):
        html = (b"&#34;workScheduleByDays&#34;:[{&#34;workScheduleByDaysElement&#34;:"
                b"[&#34;TWO_ON_TWO_OFF&#34;]}]")
        assert he._schedule_note(html) == "График (hh): TWO_ON_TWO_OFF\n"

    def test_dictionary_of_all_options_is_not_the_field(self):
        # Справочник лежит под тем же ключом и перечисляет ВСЕ варианты: взять его —
        # значит приписать вакансии и 6/1, и 2/2 разом.
        html = (b"&#34;workScheduleByDays&#34;:[{&#34;id&#34;:&#34;SIX_ON_ONE_OFF&#34;,"
                b"&#34;text&#34;:&#34;6/1&#34;}],"
                b"&#34;workScheduleByDays&#34;:[&#34;FIVE_ON_TWO_OFF&#34;]")
        assert he._schedule_note(html) == "График (hh): FIVE_ON_TWO_OFF\n"

    def test_dictionary_alone_gives_nothing(self):
        html = (b"&#34;workScheduleByDays&#34;:[{&#34;id&#34;:&#34;SIX_ON_ONE_OFF&#34;,"
                b"&#34;text&#34;:&#34;6/1&#34;}]")
        assert he._schedule_note(html) == ""

    def test_empty_field_gives_nothing(self):
        assert he._schedule_note(b'"workScheduleByDays":[],"workingHours":[]') == ""

    def test_night_line_only_when_true(self):
        # «нет» в описании ничего не добавляет, а место в начале описания дорого
        assert he._schedule_note(_fields(night=True).encode()) == "Работа ночью (hh): да\n"
        assert he._schedule_note(_fields(night=False).encode()) == ""

    def test_page_without_fields(self):
        assert he._schedule_note(b"<html>no fields</html>") == ""

    def test_fetch_puts_schedule_lines_first(self):
        # Порядок важен: за строкой «График (hh)» — строка hh, а не текст
        # работодателя, иначе «…: OTHER\n2/2 …» дало бы ложную фразу смен.
        html = (_fields(["TWO_ON_TWO_OFF"], ["HOURS_12"], True, ["REMOTE"])
                + f'<div data-qa="vacancy-description">{FULL}</div>')
        s = MagicMock(); s.get.return_value = _resp(html=html)
        text, _, _ = he._fetch("https://hh.ru/vacancy/123", s)
        assert text.startswith("График (hh): TWO_ON_TWO_OFF\n"
                               "Часы в день (hh): HOURS_12\n"
                               "Работа ночью (hh): да\n"
                               "Формат работы (hh): REMOTE\n")
        assert FULL[:40] in text


class TestScheduleSummary:
    """Подпись графика для карточки: владелец читает «6/1», а не SIX_ON_ONE_OFF."""

    @staticmethod
    def _desc(**kw) -> str:
        return ("Регион: Москва\n" + he._schedule_note(_fields(**kw).encode())
                + "Текст вакансии")

    def test_six_day_week_with_hours(self):
        desc = self._desc(days=["SIX_ON_ONE_OFF"], hours=["HOURS_8", "FLEXIBLE"])
        assert he.schedule_summary(desc) == "6/1 · 8 ч или по договорённости"

    def test_shifts_with_nights(self):
        desc = self._desc(days=["TWO_ON_TWO_OFF"], hours=["HOURS_12"], night=True)
        assert he.schedule_summary(desc) == "2/2 · 12 ч · есть ночные смены"

    def test_several_options(self):
        desc = self._desc(days=["FIVE_ON_TWO_OFF", "TWO_ON_TWO_OFF"])
        assert he.schedule_summary(desc) == "5/2 или 2/2"

    @pytest.mark.parametrize("desc", [None, "", "Вакансия с LinkedIn без полей hh"])
    def test_no_fields_no_summary(self, desc):
        assert he.schedule_summary(desc) == ""
