"""Отсев по направлению владельца (pre_filter.direction_gate), 27.10.2026.

Цель владельца 26.10 — рост в ИИ и доход выше пола. Замер потока 27.10: продажи —
43% карточек hh, и большая их часть — БАДы, недвижимость, банковские продукты.
Граница, которую держит тест:
- продажи остаются, если ИИ, SaaS или собственный IT-продукт — продукт компании;
  «используете ИИ-инструменты», «опыт в SaaS», «аккредитованная IT-компания»,
  «работа в Битрикс24» продуктом не делают;
- зарплата режет только явный верх ниже пола и только без премий/KPI: «от X» —
  оклад, а не доход (поправка владельца: оклад 75k + квартальная премия ×3);
- hh судится только по полной странице: RSS-выжимка не знает ни продукта, ни премий.
Фразы — из живых вакансий hh.
"""
import pytest

from src.matcher.pre_filter import direction_gate, score_job
from src.models import Job
from src.parsers import hh_enrich as he


def _job(title, desc, smax=None, gross=None, mode=None, source="hh.ru", full=True):
    job = Job(id="hh_1", title=title, company="c", description=desc, url="u",
              source=source, salary_max=smax, salary_currency="RUB")
    if full:
        job.raw["hh_full"] = True
    if gross is not None:
        job.raw["salary_gross"] = gross
    if mode:
        job.raw["salary_mode"] = mode
    return job


@pytest.mark.parametrize("title, desc", [
    ("Менеджер по продажам БАДов", "Продаём БАДы. ✅ используете ИИ-инструменты в работе"),
    ("Менеджер по продажам B2B", "Реагенты. Опыт в SaaS будет плюсом"),
    ("Менеджер по продажам", "Новостройки. Подбираем объекты с помощью аналитики и AI-подсказок"),
    ("Ведущий менеджер по продажам", "Продажи банковских продуктов и сервисов для бизнеса"),
    ("Менеджер по продажам (входящие звонки)",
     "Удалённая работа в аккредитованной IT-компании. Подключаем домашний интернет"),
    ("Менеджер по продажам мебели", "Работа в amoCRM и Битрикс24"),
    ("Менеджер по продажам / Менеджер по партнерам", "Знание СРМ, CMS, иных ИТ-продуктов"),
    ("Junior Sales Manager", "Crypto exchange, sell to traders"),
])
def test_sales_without_ai_product_cut(title, desc):
    assert direction_gate(_job(title, desc)) == "продажи без ИИ/SaaS/IT-продукта"


@pytest.mark.parametrize("title", [
    "Менеджер по продажам обучения нейросетям (ИИ)",
    "Менеджер по продажам онлайн-обучения в области ИИ",
    "Менеджер по продажам на вводном уроке (обучение по Нейросетям для детей)",
    "Менеджер по продажам через диагностики (видео встречи)",
])
def test_course_sales_cut_even_with_ai(title):
    assert direction_gate(_job(title, "Онлайн-школа")) == "продажа курсов/обучения"


@pytest.mark.parametrize("title, desc", [
    ("Менеджер по продажам AI-продукта", "Голосовые агенты для бизнеса"),
    ("Менеджер по продажам B2B", "Goulash.tech — IT-компания, развиваем SaaS-продукт для ресторанов"),
    ("Менеджер по продажам (IT, B2B)", "Sellmonitor — ведущий сервис аналитики и продвижения на маркетплейсах"),
    ("Менеджер по продажам", "Мы, Minervasoft, разрабатываем решения в области управления знаниями"),
    ("Менеджер по продажам ПО", "Мы — разработчик собственного программного обеспечения «Онлайн ПТО»"),
    ("Менеджер по продажам amoCRM (тёплая база)", "Продаём лицензии и внедрение"),
    ("Менеджер по продажам IT-решений (IaaS, Cloud)", ""),
    ("Менеджер по работе с клиентами (без продаж)", "Сопровождение клиентов банка"),
    ("Sales Support Specialist", "Support the sales team in CRM"),
    ("Специалист технической поддержки", "Продаём БАДы"),
])
def test_ai_or_it_product_and_non_sales_kept(title, desc):
    assert direction_gate(_job(title, desc)) is None


def test_hh_without_full_page_is_not_judged():
    """RSS-выжимка: отказ по ней не дообогащается, поэтому решать рано."""
    assert direction_gate(_job("Менеджер по продажам БАДов", "Продаём БАДы", full=False)) is None


def test_non_hh_sources_judged_by_their_text():
    job = _job("Sales Manager", "Real estate agency", source="linkedin.com", full=False)
    assert direction_gate(job) == "продажи без ИИ/SaaS/IT-продукта"


class TestSalaryFloor:
    def test_explicit_top_below_floor_cut(self):
        reason = direction_gate(_job("Специалист поддержки", "Отвечать в чате", smax=80000))
        assert reason and reason.startswith("верх зарплаты 80k")

    def test_gross_converted_to_net(self):
        # 110 000 до вычета = 95 700 на руки
        assert direction_gate(_job("Специалист поддержки", "Чат", smax=110000, gross=True))
        assert direction_gate(_job("Специалист поддержки", "Чат", smax=110000, gross=False)) is None

    @pytest.mark.parametrize("desc", [
        "Оклад + квартальная премия",
        "Ежемесячные бонусы по итогам KPI",
        "Доход: оклад и процент от продаж",
        "Прозрачная система мотивации",
    ])
    def test_variable_pay_keeps(self, desc):
        assert direction_gate(_job("Специалист поддержки", desc, smax=75000)) is None

    def test_only_from_never_cut(self):
        """«от 50 000» — оклад, а не доход."""
        job = _job("Специалист поддержки", "Чат")
        job.salary_min = 50000
        assert direction_gate(job) is None

    def test_shift_or_hour_pay_not_monthly(self):
        assert direction_gate(_job("Оператор", "Чат", smax=3000, mode="SHIFT")) is None
        assert direction_gate(_job("AI-разработчик", "n8n", smax=2000, mode="HOUR")) is None

    def test_foreign_currency_ignored(self):
        job = _job("Support", "Chat", smax=900)
        job.salary_currency = "USD"
        assert direction_gate(job) is None


def test_score_job_rejects_all_roles_with_reason():
    res = score_job(_job("Менеджер по продажам БАДов", "Продаём БАДы, удалённо"))
    assert not res["best"]["passed_gate"]
    assert {r["reasons"][0] for r in res["all"]} == {"продажи без ИИ/SaaS/IT-продукта"}


class TestEnrichMarks:
    def test_compensation_mode_parsed(self):
        html = (b'<div data-state="{&#34;compensation&#34;:{&#34;from&#34;:28500,&#34;to&#34;:90000,'
                b'&#34;currencyCode&#34;:&#34;RUR&#34;,&#34;gross&#34;:true,&#34;mode&#34;:&#34;MONTH&#34;}}">')
        comp = he._compensation(html)
        assert comp["mode"] == "MONTH" and comp["to"] == 90000

    def test_apply_compensation_stores_mode(self):
        job = Job(id="hh_1", title="t", company="c", description="", url="u", source="hh.ru")
        he._apply_compensation(job, {"from": 100, "to": 200, "currency": "RUB",
                                     "gross": False, "mode": "SHIFT"})
        assert job.raw["salary_mode"] == "SHIFT"
