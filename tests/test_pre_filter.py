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


class TestProfileFallback:
    """Профиль личный и в .gitignore → в свежем клоне/CI его нет.
    Импорт и загрузка не должны падать (на этом падал CI)."""

    def test_missing_profile_gives_empty_stoplist(self, tmp_path, monkeypatch):
        import src.matcher.pre_filter as pf
        monkeypatch.setattr(pf, "_PROFILE_DIR", tmp_path)
        assert pf._load_avoid_keywords() == set()

    def test_falls_back_to_example(self, tmp_path, monkeypatch):
        import json as _json

        import src.matcher.pre_filter as pf
        (tmp_path / "preferences.json.example").write_text(
            _json.dumps({"industries_avoid": ["Gambling"]}), encoding="utf-8"
        )
        monkeypatch.setattr(pf, "_PROFILE_DIR", tmp_path)
        assert pf._load_avoid_keywords() == {"gambling"}

    def test_real_file_wins_over_example(self, tmp_path, monkeypatch):
        import json as _json

        import src.matcher.pre_filter as pf
        (tmp_path / "preferences.json").write_text(
            _json.dumps({"industries_avoid": ["Real"]}), encoding="utf-8"
        )
        (tmp_path / "preferences.json.example").write_text(
            _json.dumps({"industries_avoid": ["Example"]}), encoding="utf-8"
        )
        monkeypatch.setattr(pf, "_PROFILE_DIR", tmp_path)
        assert pf._load_avoid_keywords() == {"real"}


class TestDedupe:
    """M2: одна роль с разных бордов = разные id → проходила дедуп по id.
    Реальный случай 25.07: Ripple ×3, Coinbase Risk & Monitoring ×2."""

    def test_same_company_and_title_collapses(self):
        from src.matcher.pre_filter import dedupe_key
        a = dedupe_key("Ripple", "Professional Services Consultant")
        b = dedupe_key("ripple", "Professional  Services   Consultant")
        assert a == b

    def test_location_suffix_ignored(self):
        from src.matcher.pre_filter import dedupe_key
        a = dedupe_key("Coinbase", "Risk & Monitoring Analyst IV (EMEA)")
        b = dedupe_key("Coinbase", "Risk & Monitoring Analyst IV (Remote)")
        assert a == b

    def test_different_roles_kept(self):
        from src.matcher.pre_filter import dedupe_key
        assert dedupe_key("Ripple", "Treasury Analyst") != \
            dedupe_key("Ripple", "Support Specialist")

    def test_dedupe_jobs_keeps_first_and_order(self):
        from src.matcher.pre_filter import dedupe_jobs
        from src.models import Job

        def j(jid, title, company):
            return Job(id=jid, title=title, company=company, description="",
                       url="http://x", source="test")

        pairs = [
            (j("1", "Ops Specialist", "Ripple"), None),
            (j("2", "Ops Specialist", "Ripple"), None),   # дубль
            (j("3", "Support Agent", "Nansen"), None),
        ]
        out = dedupe_jobs(pairs)
        assert [p[0].id for p in out] == ["1", "3"]


class TestOnsitePenalty:
    """«На месте работодателя» — стандартная формулировка HH для офиса.
    Её отсутствие в списке означало, что офисные вакансии с HH шли без штрафа
    (найдено 30.07 на реальной вакансии QA/техподдержка в Москве)."""

    def test_hh_standard_office_phrase_caught(self):
        from src.matcher.pre_filter import CRITERIA, _hits
        assert _hits(CRITERIA["onsite_penalty"], "полный день на месте работодателя")[0]

    def test_back_office_not_penalized(self):
        # «бэк-офис» — профильная ops-роль, а не признак офисной работы
        from src.matcher.pre_filter import CRITERIA, _hits
        assert _hits(CRITERIA["onsite_penalty"], "специалист бэк-офиса, удалённо")[0] == 0

    def test_remote_beats_office_mention(self):
        # Упоминание офиса не должно штрафовать, если вакансия удалённая
        from src.matcher.pre_filter import score_vacancy
        res = score_vacancy(
            "Специалист технической поддержки",
            "Финтех-платформа. Поддержка пользователей, SLA. Удалённо, офис в Москве опционально.",
            "support_fintech",
        )
        assert "только офис" not in " ".join(res["reasons"])

    def test_office_vacancy_penalized(self):
        from src.matcher.pre_filter import score_vacancy
        office = score_vacancy(
            "QA ручной тестировщик/техподдержка",
            "Ручное тестирование, первая линия поддержки криптобиржи. "
            "Москва, на месте работодателя, график 5/2.",
            "qa_web3",
        )
        remote = score_vacancy(
            "QA ручной тестировщик/техподдержка",
            "Ручное тестирование, первая линия поддержки криптобиржи. Удалённо.",
            "qa_web3",
        )
        assert office["score"] < remote["score"]


class TestCitizenshipBarrier:
    """Кандидат — гражданин РФ с разрешением на работу только в РФ.
    Вакансии с требованием паспорта/гражданства ЕС/США недостижимы (найдено
    02.08 на реальной вакансии: remote + «valid EU passport» = 84 балла)."""

    @pytest.mark.parametrize("text", [
        "Needs to have a valid EU passport",
        "EU citizenship is required for this role",
        "Applicants must be US citizens or green card holders",
        "You must have the right to work in the EU",
        "Candidates should be eligible to work in the United Kingdom",
        "требуется гражданство ЕС",
    ])
    def test_barrier_detected(self, text):
        from src.matcher.pre_filter import _citizenship_barrier
        assert _citizenship_barrier(text.lower()) is True

    @pytest.mark.parametrize("text", [
        "remote position, we hire worldwide",
        "eligible to work in Russia",
        "право работать в РФ",
        "we provide visa support and relocation assistance",
        "passport required for business trips",
    ])
    def test_no_false_positive(self, text):
        from src.matcher.pre_filter import _citizenship_barrier
        assert _citizenship_barrier(text.lower()) is False

    def test_remote_vacancy_with_eu_passport_is_gated(self):
        from src.matcher.pre_filter import score_vacancy
        res = score_vacancy(
            "Russian Speaking Crypto Support Specialist",
            "Russian speaking support for a crypto exchange, wallets, transactions. "
            "Requirements: native Russian, valid EU passport required. Fully remote.",
            "web3_support",
        )
        assert res["passed_gate"] is False
        assert res["recommend"] is False
