"""Два боевых случая 26.08.2026, оба доехали до Telegram и не должны были.

1. «Требуется специалист по межбиржевой торговле в fintech-проект»: оклада нет,
   «дальнейшая работа может вестись с привлечением собственных средств». AI дал
   90/100 — по навыкам совпадение идеальное, а правила отсекать работу за счёт
   кандидата не было.
2. Та же вакансия пришла ДВАЖДЫ из разных Telegram-каналов. В поле `company` у
   телеграм-вакансий лежит канал, а не работодатель, поэтому дедуп считал их
   разными.
"""
from src.matcher.pre_filter import dedupe_jobs, passes_hard_gates
from src.models import Job

_ARB = (
    "Требуется специалист по межбиржевой торговле. Условия: на период обучения "
    "и практики предоставляется первоначальный капитал. Дальнейшая работа может "
    "вестись с привлечением собственных средств. Полностью удалённый формат."
)


class TestOwnCapitalIsRejected:
    def test_own_funds_phrase_blocks_vacancy(self):
        ok, reasons = passes_hard_gates("Специалист по межбиржевой торговле",
                                        _ARB, "crypto_ops")
        assert not ok, f"вакансия за счёт своих средств прошла гейт: {reasons}"

    def test_deposit_alone_is_not_a_stopword(self):
        """Ложное срабатывание, которое замер поймал: «депозиты» как метрика.

        Проверяем именно СТОП-СЛОВО, а не проход целиком: вакансия может не
        пройти гейт по другой причине, и это нормально.
        """
        text = ("Специалист поддержки криптобиржи. Понимание ключевых метрик: "
                "регистрации, депозиты, retention, LTV. Работа с антифродом.")
        _, reasons = passes_hard_gates("Специалист поддержки", text, "web3_support")
        assert not any("hard-exclude" in r for r in reasons), \
            f"слово «депозиты» сработало как стоп-слово: {reasons}"

    def test_security_products_are_not_own_funds(self):
        """«Собственные СРЕДСТВА ЗАЩИТЫ» — не работа за свой счёт."""
        text = ("Мы создаем собственные средства защиты информации для крипто-"
                "инфраструктуры. Поддержка пользователей, удалённо.")
        _, reasons = passes_hard_gates("Специалист поддержки", text, "web3_support")
        assert not any("hard-exclude" in r for r in reasons), \
            f"средства защиты приняты за деньги кандидата: {reasons}"


class TestTelegramDedupe:
    def _tg(self, jid, channel):
        return Job(id=jid, title="Требуется специалист по межбиржевой торговле",
                   company=f"@{channel}", description=_ARB,
                   url=f"https://t.me/{channel}/1", source=f"telegram:{channel}")

    def test_same_vacancy_from_two_channels_collapses(self):
        jobs = [self._tg("tg_a", "cryptovakansii"), self._tg("tg_b", "opento_crypto")]
        assert len(dedupe_jobs(jobs)) == 1, "дубль из второго канала не схлопнулся"

    def test_different_vacancies_in_one_channel_survive(self):
        a = self._tg("tg_a", "cryptovakansii")
        b = self._tg("tg_b", "cryptovakansii")
        b.title = "Менеджер по продажам B2B"
        assert len(dedupe_jobs([a, b])) == 2, "разные вакансии схлопнулись в одну"

    def test_real_employers_are_still_distinguished(self):
        """Одинаковая должность у РАЗНЫХ работодателей — не дубль."""
        a = Job(id="gh_1", title="Support Specialist", company="Kraken",
                description="x", url="u", source="greenhouse")
        b = Job(id="gh_2", title="Support Specialist", company="Bybit",
                description="x", url="u", source="greenhouse")
        assert len(dedupe_jobs([a, b])) == 2
