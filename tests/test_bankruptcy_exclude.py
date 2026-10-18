"""Банкротство физлиц — отсев по заголовку и по описанию (criteria.yaml), 18.10.2026.

Повод — «Специалист по продажам услуг, БФЛ» (Бином Права, hh 138567118, 68/100):
владелец эту сферу не рассматривает, а продажи «списания долгов» на входящих
формально проходят роль sales_remote. Граница, которую держит тест: голое
«банкротство» в описании AML/рисков и «списание кредиторской задолженности»
в казначействе — не повод резать вакансию.
"""
import pytest

from src.matcher.pre_filter import CRITERIA, passes_hard_gates
from src.matcher.title_triage import title_triage

SALES_TEXT = ("Менеджер по продажам. Входящие заявки, без холодных звонков, удалённо. "
              "CRM, B2B, финтех, платежи. ")


@pytest.mark.parametrize("title", [
    "Специалист по продажам услуг, БФЛ",
    "Менеджер по продажам (Банкротство, без поиска)",
    "Менеджер по сопровождению клиентов (бфл)",
    "Эксперт по продажам в сферу банкротства",
])
def test_bankruptcy_titles_rejected_for_every_role(title):
    # «банкрот*» — в title-exclude, «бфл» — в hard-exclude (тот проверяет и
    # заголовок). Годится любая из двух причин.
    for role in CRITERIA["roles"]:
        ok, reasons = passes_hard_gates(title, SALES_TEXT, role)
        assert not ok and reasons[0].startswith(("title-exclude", "hard-exclude")), (role, reasons)


@pytest.mark.parametrize("title", ["Менеджер по продажам (БФЛ)", "Менеджер по продажам (Банкротство)"])
def test_bankruptcy_title_dropped_before_description(title):
    assert title_triage(title) == "drop"


# Живые формулировки из описаний вакансий с нейтральным заголовком.
@pytest.mark.parametrize("phrase", [
    "основатель компании по банкротству физических лиц",
    "Проводим процедуры банкротства граждан под ключ",
    "помогает людям законно списать долги через банкротство",
    "в соответствии с федеральным законом о банкротстве физических лиц",
    "Опыт продаж услуг от года: юридических, БФЛ",
])
def test_bankruptcy_description_rejected(phrase):
    ok, reasons = passes_hard_gates("Менеджер по продажам", SALES_TEXT + phrase, "sales_remote")
    assert not ok and reasons[0].startswith("hard-exclude"), reasons


@pytest.mark.parametrize("phrase", [
    "проверка контрагентов на банкротство и судебные дела",
    "мониторинг банкротств юридических лиц",
    "списание кредиторской задолженности, платёжный календарь",
    "анализ долговой нагрузки контрагентов",
])
def test_neighbouring_finance_phrases_pass(phrase):
    ok, reasons = passes_hard_gates("Менеджер по продажам", SALES_TEXT + phrase, "sales_remote")
    assert ok, reasons
