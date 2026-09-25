"""Сопровождение действующих клиентов — целевая роль, а не смежная.

Повод 25.09.2026: «Операционный + клиентский менеджер» (Еком Глобал, удалённо,
от 120k, маркетплейсы) получал 40 при пороге 42 — «клиентский менеджер» стоял в
titles_weak. Замер правки на 4914 вакансиях: +3 рекомендованных, потеряно 0.
Расширение домена (IT-компании, EdTech, AI-продукт) замерялось отдельно и
отклонено: 13 из 17 добавок — продажи в онлайн-школах на горячих лидах.
"""
import pytest

from src.matcher.pre_filter import passes_hard_gates, score_vacancy

_ECOM = (
    "Ищем человека, который ведёт своих клиентов, сам разбирается в операционке "
    "и превращает повторяющиеся проблемы в процессы. Работаем с Ozon, Wildberries, "
    "маркетплейс-интеграции. Оклад + бонус, привязанный к удержанию и росту твоих "
    "клиентов. Онбординг новых клиентов, снижение оттока. Полностью удалённо."
)


def test_client_manager_passes_threshold():
    r = score_vacancy("Операционный + клиентский менеджер", _ECOM, "sales_remote")
    assert r["recommend"], r
    assert "сильное совпадение по должности" in r["reasons"]


@pytest.mark.parametrize("title", [
    "Менеджер по сопровождению клиентов",
    "Key Account Manager",
    "Менеджер по работе с ключевыми клиентами",
    "Менеджер по успеху клиентов",
])
def test_retention_titles_pass_role_gate(title):
    ok, reasons = passes_hard_gates(title, _ECOM, "sales_remote")
    assert ok, f"«{title}» не прошёл гейт sales_remote — {reasons}"


def test_bare_customer_success_in_text_is_not_a_title():
    """titles_strong ищется по всему тексту. Упоминание customer success в описании
    Product Manager не должно делать его «сильным совпадением по должности»:
    на замере так прошли DeHopper Product Manager (20 -> 60) и Wolt."""
    text = ("Web3 messenger, remote. Work closely with sales and customer success "
            "teams to shape the roadmap.")
    r = score_vacancy("Product Manager", text, "sales_remote")
    assert "сильное совпадение по должности" not in r["reasons"], r


def test_english_retention_words_give_no_boost():
    """retention/churn/onboarding по-английски поднимали зарубежные офисные CSM
    (Elliptic 26 -> 44) — бонус только за русские слова."""
    text = ("Customer Success Manager for our fintech SaaS. Own retention, reduce "
            "churn, lead onboarding, track NPS.")
    r = score_vacancy("Customer Success Manager", text, "sales_remote")
    boosts = " ".join(x for x in r["reasons"] if "релевантные навыки" in x)
    for w in ("retention", "churn", "onboarding", "nps"):
        assert w not in boosts, r
