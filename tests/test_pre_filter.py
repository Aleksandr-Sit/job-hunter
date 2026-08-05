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


class TestLongDescriptionTruncation:
    """Парсеры резали описание на 2000 символов, а требования в длинных
    вакансиях стоят в конце. Реальный случай 05.08.2026: Bybit «[Fiat] Fiat
    Operations Specialist - Brazil» — описание 10 941 символ, требование
    «Fluency in English and Portuguese» на позиции 9 996 → гейт языков его
    не видел, вакансия ушла кандидату."""

    def test_limit_covers_long_vacancies(self):
        from src.models import MAX_DESCRIPTION_CHARS
        assert MAX_DESCRIPTION_CHARS >= 11000  # реальный кейс был 10 941

    def test_foreign_language_at_end_is_caught(self):
        from src.matcher.pre_filter import score_vacancy
        long_intro = "About Bybit. We are a leading crypto exchange. " * 200
        desc = long_intro + (
            "Languages: Fluency in both English and Portuguese is required "
            "(written and verbal). Additional languages are a plus."
        )
        assert len(desc) > 9000
        res = score_vacancy("Fiat Operations Specialist - Brazil", desc, "crypto_ops")
        assert res["passed_gate"] is False
        assert "язык" in res["reasons"][0]

    def test_ai_sample_includes_tail(self):
        """В промпт AI должен попадать и конец описания (там требования)."""
        from src.models import Job
        job = Job(id="1", title="T", company="C",
                  description="НАЧАЛО " * 200 + "ТРЕБОВАНИЯ_В_КОНЦЕ",
                  url="u", source="s")
        text = job.to_text()
        assert "ТРЕБОВАНИЯ_В_КОНЦЕ" in text
        assert "НАЧАЛО" in text

    def test_short_description_not_mangled(self):
        from src.models import Job
        job = Job(id="1", title="T", company="C", description="Короткое описание",
                  url="u", source="s")
        assert "Короткое описание" in job.to_text()
        assert "пропущена середина" not in job.to_text()


class TestForeignLangSection:
    """Язык в разделе «Preferred qualifications» — НЕ требование.
    Реальный случай 05.08.2026: Fireblocks «Technical Support Engineer, APAC» —
    Mandarin на позиции 2901, заголовок «Preferred qualifications» на 2639
    (за 262 символа, окно ±60 его не видело) → подходящая вакансия резалась."""

    def test_language_in_preferred_section_not_required(self):
        from src.matcher.pre_filter import _foreign_lang_required
        t = ("requirements: 3+ years in technical support. "
             "preferred qualifications: bs/ba degree. prior experience with saas. "
             "mandarin or another language depending on your customer base "
             "(korean, japanese, bahasa). knowledge of databases, kibana.")
        assert _foreign_lang_required(t) is False

    def test_language_in_requirements_section_is_required(self):
        from src.matcher.pre_filter import _foreign_lang_required
        t = ("preferred qualifications: bs degree. "
             "requirements: fluency in both english and portuguese, written and verbal.")
        assert _foreign_lang_required(t) is True

    def test_plain_required_language_still_blocks(self):
        from src.matcher.pre_filter import _foreign_lang_required
        t = "languages: fluency in both english and portuguese is required (written and verbal)."
        assert _foreign_lang_required(t) is True

    def test_inline_plus_still_works(self):
        from src.matcher.pre_filter import _foreign_lang_required
        assert _foreign_lang_required("spanish is a plus for this role") is False

    def test_russian_optional_marker(self):
        from src.matcher.pre_filter import _foreign_lang_required
        t = "требования: опыт поддержки. желательно: немецкий язык как преимущество."
        assert _foreign_lang_required(t) is False

    def test_fireblocks_vacancy_passes_gate(self):
        """Итог: вакансия Fireblocks должна проходить гейт (язык лишь желателен)."""
        from src.matcher.pre_filter import passes_hard_gates
        desc = ("Technical Support Engineer for our crypto custody platform. "
                "Requirements: 3+ years in technical support, troubleshooting, "
                "customer facing experience. Remote. "
                "Preferred qualifications: BS/BA degree in Computer Science. "
                "Prior experience supporting SaaS-based products. "
                "Mandarin or another language depending on your actual customer "
                "base (Korean, Japanese, Bahasa). Knowledge of databases.")
        ok, reasons = passes_hard_gates("Technical Support Engineer", desc, "web3_support")
        assert ok is True, reasons


class TestLocationField:
    """Локация хранится отдельным полем Job.location, и score_job её не передавал
    в скоринг. Реальный случай 05.08.2026: Fireblocks «Technical Support Engineer,
    APAC», location=Singapore — в описании страна не упоминалась, штрафа за офис
    в неподходящей стране не было."""

    def _job(self, location, desc="Technical support for crypto custody platform. "
                                  "Troubleshooting, API debugging, tickets."):
        from src.models import Job
        return Job(id="1", title="Technical Support Engineer", company="C",
                   description=desc, url="u", source="s", location=location)

    def test_foreign_office_location_penalized(self):
        from src.matcher.pre_filter import score_job
        sg = score_job(self._job("Singapore"))["best"]
        assert "офис" in " ".join(sg["reasons"])

    def test_relocation_country_not_penalized(self):
        from src.matcher.pre_filter import score_job
        cy = score_job(self._job("Limassol, Cyprus"))["best"]
        assert "офис" not in " ".join(cy["reasons"])

    def test_russian_location_not_penalized(self):
        from src.matcher.pre_filter import score_job
        ru = score_job(self._job("Москва"))["best"]
        assert "офис" not in " ".join(ru["reasons"])

    def test_remote_beats_location(self):
        from src.matcher.pre_filter import score_job
        r = score_job(self._job("Singapore", "Fully remote position. Technical support, "
                                             "troubleshooting, API debugging for crypto wallets."))["best"]
        assert "офис" not in " ".join(r["reasons"])

    def test_foreign_location_scores_lower(self):
        from src.matcher.pre_filter import score_job
        sg = score_job(self._job("Singapore"))["best"]["score"]
        cy = score_job(self._job("Limassol, Cyprus"))["best"]["score"]
        assert sg < cy
