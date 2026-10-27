"""ИИ — рабочий инструмент сотрудника: пометка в карточке и прибавка в предфильтре.

Решение владельца 26.10.2026: цель — расти в ИИ, поэтому вакансия, где работодатель
ждёт, что сотрудник САМ пользуется ИИ («используете ChatGPT и Claude в работе»),
получает прибавку `ai_tools` и пометку «🤖» с фразой-доказательством. ИИ в продукте
(«мы разрабатываем AI-платформу», «работа с AI-продуктами») и в опыте («проекты
AI/ML») — не то: там ИИ — предмет работы или товар, а не инструмент. В наборе 888
карточек 22.09–26.10 пометку получают 118.

Прибавка действует на ПРЕДФИЛЬТР — пустить вакансию к AI; итоговый балл карточки
ставит AI.
"""
import pytest

from src.bot.formatter import format_job_message
from src.matcher import pre_filter as pf
from src.matcher.pre_filter import ai_tools_phrase, score_vacancy
from src.models import Job, MatchResult


class TestPhrase:
    @pytest.mark.parametrize("text,phrase", [
        ("Требования: опыт продаж. Активно используете ChatGPT и Claude в работе. "
         "Будет плюсом CRM",
         "Активно используете ChatGPT и Claude в работе"),
        ("готовность применять нейросети для ускорения рутинных задач",
         "готовность применять нейросети для ускорения рутинных задач"),
        # глагол — в заголовке списка, инструмент — пунктом под ним
        ("Используете в работе:\n• ChatGPT\n• Notion", "Используете в работе: • ChatGPT"),
        # hh 138811791
        ("✅ Используете ИИ-инструменты в ежедневной работе: аналитика, SEO, визуал, "
         "рутинные отчеты",
         "✅ Используете ИИ-инструменты в ежедневной работе: аналитика, SEO, визуал, "
         "рутинные отчеты"),
        ("You use ChatGPT daily to speed up support replies.",
         "You use ChatGPT daily to speed up support replies"),
        # «AI-модели» — инструмент, в отличие от «AI-продуктов» ниже
        ("навыки работы с генеративными AI-моделями",
         "навыки работы с генеративными AI-моделями"),
    ])
    def test_tool_use_is_found(self, text, phrase):
        assert ai_tools_phrase(text) == phrase

    @pytest.mark.parametrize("text", [
        "Мы разрабатываем AI-платформу для ритейла и ищем аккаунт-менеджера",
        "Наш продукт — AI-ассистент для юристов. Ищем менеджера по продажам.",
        "Опыт работы в проектах AI/ML от 2 лет",
        # hh 137880718: «агентство» — не «агент», «работа с AI-продуктами» — товар
        "Команда AI-агентства GINAI ищет менеджера по онлайн презентации AI продукта. "
        "Понимание специфики работы с AI-продуктами и B2B-клиентами.",
        # hh 137977568
        "Возможность работать с новым AI-продуктом на этапе выхода на рынок",
        "Ищем AI-агентов поддержки для нашего контакт-центра",
        "",
        None,
    ])
    def test_ai_as_product_or_subject_is_not_found(self, text):
        assert ai_tools_phrase(text) is None


class TestBonus:
    _TEXT = ("Формат работы (hh): REMOTE\n"
             "Менеджер по работе с клиентами в SaaS. Удержание действующих клиентов, CRM.\n"
             "Используете ChatGPT в работе каждый день.\n")

    @staticmethod
    def _score(text: str, title: str = "Аккаунт-менеджер") -> dict:
        return score_vacancy(title, text, "sales_remote")

    def test_bonus_is_the_configured_weight(self, monkeypatch):
        monkeypatch.setitem(pf._W, "ai_tools", 0)
        base = self._score(self._TEXT)
        assert not any("ИИ-инструменты" in x for x in base["reasons"])
        monkeypatch.setitem(pf._W, "ai_tools", 6)
        r = self._score(self._TEXT)
        assert r["score"] == base["score"] + 6
        assert "+6 ценят ИИ-инструменты" in r["reasons"]

    def test_title_alone_gives_no_bonus(self, monkeypatch):
        # ИИ в заголовке — это роль, а не «сотрудник сам пользуется ИИ»
        monkeypatch.setitem(pf._W, "ai_tools", 6)
        text = self._TEXT.replace("Используете ChatGPT в работе каждый день.\n", "")
        r = self._score(text, title="AI-интегратор")
        assert not any("ИИ-инструменты" in x for x in r["reasons"])

    def test_weight_is_in_criteria(self):
        assert "ai_tools" in pf._W


class TestCard:
    @staticmethod
    def _job(desc: str, source: str = "hh.ru") -> Job:
        return Job(id="hh_137767979", title="AI-интегратор", company="OsteoFace",
                   description=desc, url="https://hh.ru/vacancy/137767979",
                   source=source, location="Москва")

    @staticmethod
    def _match() -> MatchResult:
        return MatchResult(job_id="hh_137767979", score=82, why_fits=["n8n и Claude"],
                           watch_out=[], recommendation="Откликнуться")

    def test_schedule_and_ai_lines_under_company(self):
        # OsteoFace (hh 137767979): 6/1 было только в поле hh, и владелец его не видел
        desc = ("Вакансия компании: OsteoFace|Регион: Москва\n"
                "График (hh): SIX_ON_ONE_OFF\nЧасы в день (hh): HOURS_8, FLEXIBLE\n"
                "Формат работы (hh): REMOTE\n"
                "Ищем AI-интегратора. Требования: активно используете ChatGPT и Claude в "
                "работе; опыт с n8n.")
        lines = format_job_message(self._job(desc), self._match()).split("\n")
        sched = lines.index("🗓 График (hh): 6/1 · 8 ч или по договорённости")
        assert lines[sched + 1] == ("🤖 Ценят ИИ-инструменты: «Требования: активно "
                                    "используете ChatGPT и Claude в работе»")
        assert lines[sched - 1].startswith("<b>OsteoFace</b>"), "строки — под компанией"

    def test_plain_vacancy_has_no_extra_lines(self):
        msg = format_job_message(
            self._job("Customer support, remote. CRM, tickets.", source="linkedin"),
            self._match())
        assert "🗓" not in msg and "🤖" not in msg

    def test_long_phrase_is_cut(self):
        desc = ("Активно используете в своей ежедневной работе с клиентами и партнёрами "
                "самые разные сервисы и в том числе ChatGPT Claude Midjourney Perplexity "
                "и другие для анализа данных подготовки писем отчётов презентаций и "
                "коммерческих предложений без лишней рутины")
        assert len(ai_tools_phrase(desc)) > 160, "тест потерял смысл"
        line = next(x for x in format_job_message(self._job(desc), self._match()).split("\n")
                    if x.startswith("🤖"))
        phrase = line.split("«", 1)[1].removesuffix("»")
        assert phrase.endswith("…") and len(phrase) <= 161

    def test_phrase_is_html_escaped(self):
        desc = "Используете ChatGPT <и Claude> в работе каждый день"
        assert "&lt;и Claude&gt;" in format_job_message(self._job(desc), self._match())
