"""Тесты предфильтра: стемминг, гейт «7+ лет», скоринг ролей.

Покрывают баги, найденные в реальной эксплуатации (docs/HEALTH_AUDIT.md):
F10 — «N лет» в описании КОМПАНИИ («spent the last 15 years building…») ловилось
как требование опыта и молча резало релевантные ops-вакансии.
"""
import pytest

from src.matcher.pre_filter import (
    _high_exp_required,
    _matches,
    passes_hard_gates,
    score_vacancy,
)


class TestMatches:
    """`_matches`: стем (`слово*`), точное слово, фраза."""

    @pytest.mark.parametrize("form", [
        "операции", "операциям", "операционный", "оператор", "операторов",
    ])
    def test_stem_catches_russian_cases(self, form):
        assert _matches("операц*", form) or _matches("оператор*", form)

    def test_stem_anchored_at_word_start(self):
        # «кооперация» не должна матчить «операц*» — иначе ловим не тот домен
        assert not _matches("операц*", "кооперация в компании")

    def test_exact_token_not_substring(self):
        assert _matches("ops", "crypto ops specialist")
        assert not _matches("ops", "opsgenie monitoring")

    def test_phrase_is_substring(self):
        assert _matches("crypto operations", "senior crypto operations manager")


class TestHighExpRequired:
    """F10: «N лет» = требование только в контексте опыта, не истории компании."""

    @pytest.mark.parametrize("text", [
        "has spent the last 15 years building one of the most modern platforms",
        "over the last 10 years our company has grown",
        "we have been building crypto infra for 12 years",
        "celebrating 20 years of innovation",
        "2 years experience, junior friendly",
    ])
    def test_not_a_requirement(self, text):
        assert _high_exp_required(text) is False

    @pytest.mark.parametrize("text", [
        "7+ years of experience in operations",
        "minimum 8 years in a similar role",
        "requires at least 9 years experience",
        "опыт от 7 лет в операциях",
        "не менее 8 лет опыта",
    ])
    def test_is_a_requirement(self, text):
        assert _high_exp_required(text) is True


class TestHardGates:
    def test_dev_role_rejected(self):
        ok, reasons = passes_hard_gates(
            "Solidity Developer", "Build smart contracts", "crypto_ops"
        )
        assert ok is False

    def test_ops_role_with_crypto_domain_passes(self):
        ok, _ = passes_hard_gates(
            "Crypto Operations Specialist",
            "Manage on-chain operations for our exchange, remote",
            "crypto_ops",
        )
        assert ok is True

    def test_ops_role_without_crypto_domain_rejected(self):
        # Домен-гейт держит крипто-фокус (замер 25.07: смягчение = генерик-шум)
        ok, reasons = passes_hard_gates(
            "Operations Specialist", "Manage office operations", "crypto_ops"
        )
        assert ok is False
        assert "крипто" in reasons[0].lower()

    def test_qa_role_russian_passes(self):
        ok, _ = passes_hard_gates(
            "Инженер по тестированию",
            "Тестирование криптобиржи, ручное тестирование, удалённо",
            "qa_web3",
        )
        assert ok is True


class TestScoreVacancy:
    def test_gate_failure_scores_zero(self):
        res = score_vacancy("Solidity Developer", "smart contracts", "crypto_ops")
        assert res["passed_gate"] is False
        assert res["score"] == 0
        assert res["recommend"] is False

    def test_strong_title_and_remote_recommends(self):
        res = score_vacancy(
            "Crypto Operations Specialist",
            "Remote. On-chain operations, transaction monitoring, CEX/DEX.",
            "crypto_ops",
        )
        assert res["passed_gate"] is True
        assert res["recommend"] is True

    def test_score_bounded_0_100(self):
        res = score_vacancy(
            "Crypto Operations Specialist",
            "Remote. staking bridge liquidity settlement custody treasury kyc aml "
            "on-chain multichain evm ethereum solana. Russian speaking team.",
            "crypto_ops",
        )
        assert 0 <= res["score"] <= 100

    def test_russian_vacancy_scores_like_english(self):
        """EN↔RU паритет: русская вакансия не должна проигрывать эквивалентной англ."""
        en = score_vacancy(
            "Crypto Operations Specialist",
            "Remote. Crypto operations, transaction monitoring, exchange.",
            "crypto_ops",
        )
        ru = score_vacancy(
            "Специалист по крипто-операциям",
            "Удалённо. Крипто-операции, мониторинг транзакций, биржа.",
            "crypto_ops",
        )
        assert ru["passed_gate"] is True
        assert ru["recommend"] == en["recommend"]
