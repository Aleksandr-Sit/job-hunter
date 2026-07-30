"""Тесты Telegram-парсера: чистка заголовков и отсев постов-резюме.

Покрывают F1 (docs/HEALTH_AUDIT.md): заголовками вакансий становились «✍️»,
«#Вакансия», «Резюме:» — мусор уходил в AI и в карточки.
"""
import pytest

from src.parsers.telegram_parser import _is_resume_post, _pick_title


class TestPickTitle:
    def test_skips_leading_emoji(self):
        assert _pick_title(["✍️", "Crypto Support Specialist", "Remote"]) == \
            "Crypto Support Specialist"

    def test_skips_generic_header(self):
        # «#Вакансия» — не роль; берём следующую содержательную строку
        assert _pick_title(["#Вакансия", "Operations Manager"]) == "Operations Manager"

    def test_strips_leading_hash_and_keeps_role(self):
        assert _pick_title(["🚀 Junior DeFi Operations"]) == "Junior DeFi Operations"

    def test_requires_min_letters(self):
        # строки без букв пропускаются
        assert _pick_title(["123", "!!!", "Community Manager"]) == "Community Manager"

    def test_fallback_when_all_junk(self):
        assert _pick_title(["🔥", "💬"]) != ""

    def test_empty_lines(self):
        assert _pick_title([]) == "Job opening"


class TestIsResumePost:
    @pytest.mark.parametrize("text", [
        "резюме: crypto ops, 5 лет опыта, ищу удаленную работу",
        "#резюме web3 developer, portfolio",
        "open to work: crypto support specialist",
        "looking for work in defi",
    ])
    def test_resume_posts_detected(self, text):
        assert _is_resume_post(text) is True

    @pytest.mark.parametrize("text", [
        "вакансия: crypto support, присылайте резюме на почту",
        "ищем operations manager в crypto проект, remote",
        "hiring: web3 community manager",
    ])
    def test_real_vacancies_not_filtered(self, text):
        assert _is_resume_post(text) is False
