"""Словарь ролей должен знать слова, на которых он уже спотыкался.

Повод 31.08.2026: разбор отсеянных заголовков LinkedIn показал, что словарь не
знает целых семейств должностей. Из-за этого живые вакансии не проходили даже
ролевой гейт: Client Success Specialist, Client Onboarding Analyst, Middle Office
Analyst, Business Development Officer, Account Executive, Senior KYB Analyst.

Замер добавления: на 2295 вакансиях HH+ATS +7 рекомендованных, потеряно 0;
на 989 карточках LinkedIn +22 прошедших по заголовку, потеряно 0.
"""
import pytest

from src.matcher.pre_filter import passes_hard_gates

# (заголовок, роль, за что отвечает)
CASES = [
    ("Account Executive", "sales_remote", "AE — продавец-закрывающий"),
    ("Business Development Officer", "sales_remote", "BD-семейство"),
    ("Business Development Representative (Remote)", "sales_remote", "BDR"),
    ("OTC Business Development Manager", "sales_remote", "BDM в крипте"),
    ("Client Success Specialist", "support_fintech", "CSM-семейство"),
    ("Client Onboarding Analyst", "support_fintech", "онбординг клиентов"),
    ("Middle Office Analyst", "support_fintech", "мидл-офис"),
    ("Back Office Processing Specialist", "support_fintech", "бэк-офис"),
    ("Merchant Implementation Manager (Payments)", "support_fintech", "внедрение"),
    ("Implementation Analyst", "support_fintech", "внедрение"),
    ("Senior KYB Analyst", "aml_compliance", "KYB — проверка юрлиц"),
    ("Fraud Risk Strategy, Detection and Resolution", "aml_compliance", "антифрод"),
]

# Достаточный контекст, чтобы не упереться в домен-гейт: проверяем РОЛЕВОЙ гейт.
_CTX = ("Remote position at a fintech and crypto company. Work with clients, "
        "CRM, reporting and daily operations. Blockchain payments platform.")


@pytest.mark.parametrize("title,role,why", CASES)
def test_role_vocabulary_knows_title(title, role, why):
    ok, reasons = passes_hard_gates(title, _CTX, role)
    assert ok, f"{why}: «{title}» не прошёл ролевой гейт {role} — {reasons}"


def test_account_executive_is_not_treated_as_account_manager():
    """AE и аккаунт-менеджер — разные профессии с разной медианой (120к против 80к).

    Тест фиксирует, что «account executive» ведёт в продажи, а не подменяется
    аккаунт-менеджментом: их объединение уже приводило к неверной рекомендации.
    """
    from src.matcher.pre_filter import CRITERIA
    sales = CRITERIA["roles"]["sales_remote"]["must_role"]
    assert "account executive" in sales
