"""Мусор из Telegram-каналов не должен доходить до AI.

Все кейсы взяты из свежей пачки 15.08.2026, где они реально прошли предфильтр:
«SPONSORED JOB POST» набрал 66 баллов, «Вакансия:» ушла как заголовок карточки,
«Добрый день, друзья. Отпуск окончен» — 40 баллов.
"""
from src.parsers.telegram_parser import _generic_header, _is_promo_post, _pick_title


class TestGenericHeader:
    def test_bare_word(self):
        assert _generic_header("Вакансия")
        assert _generic_header("Hiring")

    def test_with_trailing_punctuation(self):
        # Ровно тот случай, что уходил в AI: двоеточие делало заголовок «содержательным».
        for s in ("Вакансия:", "Вакансия :", "ВАКАНСИЯ!", "Vacancy -", "Hiring —"):
            assert _generic_header(s), s

    def test_real_role_is_not_generic(self):
        for s in ("Support Manager", "Вакансия менеджера поддержки",
                  "AML аналитик", "Community Manager"):
            assert not _generic_header(s), s


class TestPickTitle:
    def test_skips_generic_header_with_colon(self):
        assert _pick_title(["Вакансия:", "Support Manager в криптобиржу"]) == \
            "Support Manager в криптобиржу"

    def test_keeps_first_meaningful_line(self):
        assert _pick_title(["🔥🔥🔥", "#hiring", "AML Analyst в Bybit"]) == "AML Analyst в Bybit"


class TestPromoPost:
    def test_sponsored(self):
        assert _is_promo_post("sponsored job post — best crypto jobs here")
        assert _is_promo_post("ищем менеджера. #реклама erid: 2abc")

    def test_channel_chatter_without_vacancy(self):
        assert _is_promo_post(
            "добрый день, друзья. отпуск окончен, пора возвращаться к работе")
        assert _is_promo_post("всем привет! напоминаем, что канал переехал")

    def test_greeting_followed_by_real_vacancy_is_kept(self):
        # Перелов дороже: пост может начинаться с приветствия и содержать вакансию.
        assert not _is_promo_post(
            "всем привет! ищем support manager в крипто-проект, удалённо, от 2000$")
        assert not _is_promo_post(
            "добрый день, друзья. вакансия: aml аналитик, remote")

    def test_ordinary_vacancy_untouched(self):
        for s in ("ищем комьюнити-менеджера в web3 проект, удалённо",
                  "hiring: customer support specialist, remote, crypto",
                  "требуется оператор поддержки, график 5/2"):
            assert not _is_promo_post(s), s
