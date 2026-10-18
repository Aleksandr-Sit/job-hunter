"""Исключения только по заголовку (criteria.yaml: global_title_exclude), 18.10.2026.

Повод — «Russian-Speaking Social Media Content Moderators» (LinkedIn, AI 58):
владелец модерацию контента не рассматривает. Две границы, которые держит тест:
Community/Discord Moderator — входная роль Web3 Support, её резать нельзя; фраза
«content moderation» в ОПИСАНИИ поддержки — не повод резать вакансию.
"""
import pytest

from src.matcher.pre_filter import CRITERIA, passes_hard_gates
from src.matcher.title_triage import title_triage

SUPPORT_TEXT = ("Customer support for our crypto exchange users via tickets and chat. "
                "Remote. Russian speaking. Fintech, payments, blockchain.")


@pytest.mark.parametrize("title", [
    "Russian-Speaking Social Media Content Moderators",
    "Content Moderation - Bilingual Russian/English",
    "Content Moderator (Russian)",
    "Модератор контента (удалённо)",
    "Контент-модератор",
    "Контент-модераторы на проект",
    "Специалист по модерации контента",
])
def test_content_moderation_titles_rejected_for_every_role(title):
    for role in CRITERIA["roles"]:
        ok, reasons = passes_hard_gates(title, SUPPORT_TEXT, role)
        assert not ok and reasons[0].startswith("title-exclude"), (role, reasons)


def test_moderation_title_dropped_before_description():
    assert title_triage("Russian-Speaking Social Media Content Moderators") == "drop"


@pytest.mark.parametrize("title", [
    "Community Moderator (Web3)",
    "Discord Moderator",
    "Telegram Community Moderator, Crypto",
])
def test_community_moderators_not_excluded(title):
    for role in CRITERIA["roles"]:
        _, reasons = passes_hard_gates(title, SUPPORT_TEXT, role)
        assert not reasons[0].startswith("title-exclude"), (role, reasons)


def test_phrase_in_description_does_not_reject():
    text = SUPPORT_TEXT + " You will also help with content moderation escalations."
    ok, reasons = passes_hard_gates("Customer Support Specialist", text, "support_fintech")
    assert ok, reasons
