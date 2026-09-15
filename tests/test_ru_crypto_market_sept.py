"""Отбор под рынок РФ-крипты: словарь, стоп-лист работодателей, слежка HH.

Повод — обновление карты рынка 15.09.2026 (docs/RU_CRYPTO_MARKET_MAP.md, раздел 6):
- 282-ФЗ ввёл термин «цифровая валюта», и банки пишут вакансии этим языком;
- операционные крипто-роли в РФ называются «казначей», «бэк-офис», «менеджер
  обменного сервиса», «обработка заявок» — без слова «операции»;
- «поддержк*» не видел «техподдержку» (стем ищет начало слова);
- часть РФ-площадок попала под санкции UK/ЕС в 2026 — вакансии от них не нужны;
- hh.ru молча игнорирует несуществующий employer_id и отдаёт чужую выдачу.
"""
import pytest

from src.matcher.pre_filter import (
    CRITERIA,
    _blocked_employer,
    is_soft_reject,
    passes_hard_gates,
    score_job,
    score_vacancy,
)
from src.models import Job

# (заголовок, контекст, роль, за что отвечает) — контекст без слов, которые словарь
# знал и раньше, чтобы тест ловил именно новые термины.
GATE_CASES = [
    ("Специалист техподдержки по обработке заявок",
     "Сервис обмена криптовалют, заявки клиентов в USDT.",
     "web3_support", "«техподдержк*» — «поддержк*» мимо"),
    ("Менеджер обменного сервиса",
     "Сервис обмена цифровых валют. Обработка заявок клиентов, сверка платежей.",
     "crypto_ops", "роль «обработка заявок» + домен «цифровых валют»"),
    ("Специалист казначейства",
     "Финтех на стыке цифровых финансовых активов и традиционного рынка.",
     "crypto_ops", "казначейство как операционная роль"),
    ("P2P manager",
     "USDT settlement desk for clients.",
     "crypto_ops", "P2P + домен USDT"),
    ("Фрод-аналитик — направление «Антифрод цифровых валют»",
     "Мониторинг операций клиентов.",
     "aml_compliance", "антифрод по-русски"),
    ("Специалист поддержки брокерского обслуживания",
     "Консультации клиентов по счетам и цифровому депозитарию.",
     "support_fintech", "волна 2: брокерское обслуживание без слова «крипто»"),
]


@pytest.mark.parametrize("title,ctx,role,why", GATE_CASES)
def test_ru_crypto_vocabulary_passes_gate(title, ctx, role, why):
    ok, reasons = passes_hard_gates(title, ctx, role)
    assert ok, f"{why}: «{title}» не прошёл гейт {role} — {reasons}"


@pytest.mark.parametrize("title", [
    "Менеджер обменного сервиса", "Специалист казначейства", "Казначей",
    "Менеджер P2P (CIS - СНГ)", "OTC Manager",
])
def test_ru_ops_titles_are_strong(title):
    res = score_vacancy(title, "Криптовалютный обменный сервис, удалённо.", "crypto_ops")
    assert "сильное совпадение по должности" in res["reasons"], res


def _job(company="", title="Специалист поддержки", source="hh.ru"):
    return Job(id="t1", title=title, company=company, source=source, url="https://x",
               description="Поддержка клиентов криптобиржи, удалённо, USDT, P2P.")


class TestEmployerBlocklist:
    def test_list_is_loaded_from_criteria(self):
        assert "abcex" in CRITERIA["employer_blocklist"]

    @pytest.mark.parametrize("company", ["ABCEX", "ООО\xa0Rapira Group", "Grinex", "HTX"])
    def test_sanctioned_company_is_rejected_for_all_roles(self, company):
        scored = score_job(_job(company=company))
        assert all(not r["passed_gate"] for r in scored["all"])
        assert "стоп-листе" in scored["best"]["reasons"][0]

    def test_block_is_hard_not_soft(self):
        # Иначе HH-пересмотр отказов потратит на неё скачивание полного описания.
        assert not is_soft_reject(score_job(_job(company="ABCEX")))

    def test_telegram_employer_in_title_is_rejected(self):
        job = _job(company="@careers_crypto", title="Support в Grinex, удалённо",
                   source="telegram")
        assert not score_job(job)["best"]["passed_gate"]

    @pytest.mark.parametrize("company", ["ABC Exchange", "Chtx Labs", "Exmoor Tech", "Bitbanker"])
    def test_whole_word_only(self, company):
        assert _blocked_employer(company, "Специалист поддержки") is None

    def test_description_mention_does_not_block(self):
        job = _job(company="Bitbanker")
        job.description += " Опыт работы с Garantex и ABCEX — плюс."
        assert _blocked_employer(job.company, job.title) is None


class TestHHEmployerWatch:
    @staticmethod
    def _parser(monkeypatch, returned):
        from src.parsers.hh_parser import HHParser
        p = HHParser()
        p.cfg = {"search_queries": [], "second_pass": False,
                 "employer_ids": [{"id": 10241526, "name": "ТРАНОМИКА"}]}
        calls = []

        def fake(query, by_date=True, employer_id=None):
            calls.append((query, employer_id))
            return returned
        monkeypatch.setattr(p, "_fetch_query", fake)
        return p, calls

    def test_own_vacancies_kept_with_nbsp_in_company(self, monkeypatch):
        own = Job(id="hh_1", title="AML Officer", company="ООО\xa0ТРАНОМИКА",
                  description="", url="u", source="hh.ru")
        p, calls = self._parser(monkeypatch, [own])
        assert [j.id for j in p.parse()] == ["hh_1"]
        assert calls == [(None, 10241526)]

    def test_foreign_results_of_wrong_id_are_dropped(self, monkeypatch):
        foreign = [Job(id=f"hh_{i}", title="Кладовщик", company="Lamoda",
                       description="", url="u", source="hh.ru") for i in range(20)]
        p, _ = self._parser(monkeypatch, foreign)
        assert p.parse() == []


class TestConfig:
    @staticmethod
    def _hh():
        import yaml
        from src.parsers.hh_parser import _CONFIG
        return yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))["parsers"]["hh"]

    def test_sanctioned_exchange_not_tracked(self):
        assert "abcex" not in {q.lower() for q in self._hh()["search_queries"]}

    def test_queries_unique(self):
        qs = [q.lower().strip() for q in self._hh()["search_queries"]]
        dupes = {q for q in qs if qs.count(q) > 1}
        assert not dupes, dupes

    def test_employer_ids_have_id_and_name(self):
        emps = self._hh().get("employer_ids") or []
        assert emps, "слежка за работодателями пуста"
        for e in emps:
            assert str(e.get("id", "")).isdigit() and e.get("name"), e
