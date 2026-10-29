"""Тесты Telegram-парсера: чистка заголовков, отсев постов-резюме, зарплата.

Покрывают F1 (docs/HEALTH_AUDIT.md): заголовками вакансий становились «✍️»,
«#Вакансия», «Резюме:» — мусор уходил в AI и в карточки.

Зарплата (29.10.2026): номер вакансии в начале поста @workingincrypto шёл в
карточку и в AI как «от 2655 USD». Строки тестов — из живых постов 12 каналов бота.
"""
import pytest

from src.parsers.telegram_parser import _extract_salary, _is_resume_post, _pick_title


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


# Пост @workingincrypto/3230: номер вакансии первым словом, оплата только процентом.
_WIC_POST = """2655 #Вакансия #Sales #Support #AI #DigitalGoods #Remote #Удаленно

Требуется менеджер по продажам и поддержке в магазин AI-подписок

▫️Гибкие смены в пределах 09:00–22:00 МСК.
▫️Процент от продаж + бонусы за результат.

Контакты: @zxcptr"""


class TestExtractSalary:
    @pytest.mark.parametrize("text, expected", [
        # с валютой и словом «зарплата»
        ("Зарплата: $2 500–3 500 в месяц в зависимости от опыта.", (2500, 3500, "USD")),
        ("ЗП от $1300/мес.", (1300, None, "USD")),
        ("Зарплата - от 180 000 ₽", (180000, None, "RUB")),
        ("Зарплата на старте: 100 USDT", (100, None, "USD")),
        ("Средний общий доход от 1400 до 2200 USDT в месяц", (1400, 2200, "USD")),
        ("Monthly earnings of $3,000-18,000, with no upper limit.", (3000, 18000, "USD")),
        ("- ЗП: ~$130,000 – $220,000 в год (гросс).", (130000, 220000, "USD")),
        ("- ЗП: ~€35,000 – €50,000 в год (gross) (оценка).", (35000, 50000, "EUR")),
        # валюта, которую не знаем (AED), — берём сумму в долларах из скобок
        ("- ЗП: AED 340K–475K/год (~$92K–129K) + equity.", (92000, 129000, "USD")),
        ("Оплата: первый месяц $1500, со второго $700+KPI", (1500, None, "USD")),
        # слово «зарплата» без знака валюты
        ("Доход в диапазоне 1300–2000: фиксированный оклад + бонус", (1300, 2000, "USD")),
        # строка-зарплата без слова «зарплата»
        ("$4,000 – $6,000 / month | Remote | Full-time", (4000, 6000, "USD")),
        ("$155k-190k/year", (155000, 190000, "USD")),
        ("$6,000+ net", (6000, None, "USD")),
        ("Удаленно, от $400 до $2000", (400, 2000, "USD")),
        ("55 000 ₽ оклад + бонусы", (55000, None, "RUB")),
        ("Оклад 150 тыс. руб + KPI", (150000, None, "RUB")),
        ("Оплата 2-3k USDT в месяц", (2000, 3000, "USD")),
    ])
    def test_real_salary_lines(self, text, expected):
        assert _extract_salary(text) == expected

    def test_vacancy_number_is_not_salary(self):
        assert _extract_salary(_WIC_POST)[:2] == (None, None)

    def test_number_then_real_salary(self):
        # номер вакансии в начале не мешает найти зарплату ниже
        text = "2650 #Вакансия #SMM\n\nТребуется SMM-менеджер\n\nЗП от $1300/мес."
        assert _extract_salary(text) == (1300, None, "USD")

    @pytest.mark.parametrize("text", [
        "1,500+ have visited our Devcon/IBW side event list",
        "Join our 100k+ member Web3 & AI community:",
        "Aptos Labs — блокчейн-платформа, привлекшая $350M+ от a16z",
        "Бюджеты от $50 000+/мес. под управлением",
        "Опыт closing сделок от $50k ACV.",
        "Зарплата: Обсуждается индивидуально",
        "Оплачиваемое обучение 5 дней, пн-пт, 09:00-17:00 МСК.",
        "Доход за 2025 год вырос вдвое",
        "Zorion provides users with a USD account, cards and an Earn product",
    ])
    def test_not_salary(self, text):
        assert _extract_salary(text)[:2] == (None, None)

    def test_skips_deal_size_and_takes_base_salary(self):
        text = ("Опыт closing сделок от $50k ACV.\n"
                "Базовый оклад: от 1,000 USD до 5,000, в зависимости от опыта.")
        assert _extract_salary(text) == (1000, 5000, "USD")
