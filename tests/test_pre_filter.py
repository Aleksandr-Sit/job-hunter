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


class TestFluentEnglishGate:
    """«Fluent English» — недостижимый барьер при A1–A2, как паспорт ЕС.
    Раньше был мягкий штраф −14, который перебивался сильным заголовком
    (замер 05.08.2026: удалённая крипто-вакансия с fluent English = 64 балла).
    Цена гейта измерена: 4 из 233 проходящих вакансий (2%), из них 0 русскоязычных."""

    @pytest.mark.parametrize("text", [
        "requirements: fluent in english; mandarin proficiency is a plus",
        "native english speaker required",
        "excellent written and spoken english",
        "english c1 required for this role",
        "требования: свободный английский",
    ])
    def test_gated(self, text):
        from src.matcher.pre_filter import _fluent_english_required
        assert _fluent_english_required(text) is True

    @pytest.mark.parametrize("text", [
        "english is a plus for this role",
        "preferred qualifications: fluent in english would be great",
        "basic english is enough, we work in russian",
    ])
    def test_not_gated(self, text):
        from src.matcher.pre_filter import _fluent_english_required
        assert _fluent_english_required(text) is False

    def test_russian_desk_not_gated(self):
        """RU-desk: английский вторичен, решение об отклике — за кандидатом."""
        from src.matcher.pre_filter import _fluent_english_required
        t = ("russian speaking support specialist for our cis users. "
             "fluent in english and russian required.")
        assert _fluent_english_required(t) is False

    def test_remote_crypto_vacancy_now_blocked(self):
        from src.matcher.pre_filter import score_vacancy
        res = score_vacancy(
            "Custody Operations Specialist",
            "Manage crypto custody operations, wallets, settlement for our exchange. "
            "Fully remote. Requirements: Fluent in English; Mandarin is a plus.",
            "crypto_ops")
        assert res["passed_gate"] is False
        assert "английск" in res["reasons"][0]


class TestRussianDeskAbroad:
    """Офис в стране вне списка релокации, НО требуется русский язык — такие
    рассматриваем: компания нанимает русскоязычных и решает визу (решение
    владельца 05.08.2026). Штраф мягче обычного офисного."""

    DESC = ("Support crypto exchange users, wallets, transactions, tickets. "
            "Requirements: {req} Office-based role.")

    def _score(self, req, location):
        from src.matcher.pre_filter import score_job
        from src.models import Job
        return score_job(Job(id="1", title="Customer Support Specialist", company="X",
                             description=self.DESC.format(req=req),
                             url="u", source="s", location=location))["best"]

    def test_russian_required_abroad_scores_higher(self):
        ru = self._score("Native Russian speaker required.", "Warsaw, Poland")
        no_ru = self._score("Strong communication skills.", "Warsaw, Poland")
        assert ru["score"] > no_ru["score"]

    def test_russian_desk_reason_shown(self):
        ru = self._score("Russian speaking team, CIS users.", "Warsaw, Poland")
        assert "русскоязычн" in " ".join(ru["reasons"])

    def test_plain_foreign_office_still_penalised_hard(self):
        r = self._score("Strong communication skills.", "Singapore")
        assert "только офис" in " ".join(r["reasons"])

    def test_russian_desk_abroad_can_pass(self):
        ru = self._score("Native Russian required for our CIS desk.", "Warsaw, Poland")
        assert ru["recommend"] is True


class TestAshbyWorkplaceType:
    """Ashby: поле isRemote недостоверно — у Elliptic 26 из 28 вакансий имеют
    workplaceType=Hybrid И isRemote=true. Гибридная роль в Гонконге приходила
    помеченной «Remote» (найдено 05.08.2026). Достоверен workplaceType."""

    def _parse(self, workplace, is_remote_flag, location="Hong Kong"):
        from src.parsers.web.ashby import AshbyParser
        item = {"id": "x", "title": "Solutions Consultant", "location": location,
                "workplaceType": workplace, "isRemote": is_remote_flag,
                "descriptionPlain": "Support enterprise clients with crypto compliance tools.",
                "jobUrl": "u", "publishedAt": "2026-08-01T00:00:00Z"}
        return AshbyParser._parse_item(AshbyParser.__new__(AshbyParser), item, "Elliptic")

    def test_hybrid_not_marked_remote(self):
        assert self._parse("Hybrid", True).is_remote is False

    def test_remote_type_marked_remote(self):
        assert self._parse("Remote", False).is_remote is True

    def test_hybrid_adds_onsite_signal_to_text(self):
        job = self._parse("Hybrid", True)
        assert "on-site presence required" in job.description

    def test_missing_workplace_falls_back(self):
        assert self._parse("", True).is_remote is True

    def test_hybrid_abroad_is_penalised(self):
        """Гибрид за рубежом должен получать офисный штраф (роль/домен проходят)."""
        from src.matcher.pre_filter import score_job
        job = self._parse("Hybrid", True, location="Hong Kong")
        job.description = ("Customer support specialist for our crypto compliance "
                           "platform. Handle client tickets, CRM, SLA, escalations. "
                           + job.description)
        assert "офис" in " ".join(score_job(job)["best"]["reasons"])


class TestNormalizeModule:
    """Общий модуль нормализации (src/parsers/normalize.py): правила формата и
    обрезки живут в одном месте, чтобы правка не чинила один парсер и не
    оставляла баг в остальных — так находились Bybit/Gemini/Elliptic."""

    def test_workplace_type_is_authoritative(self):
        from src.parsers.normalize import detect_remote
        # Ashby: workplaceType=Hybrid при isRemote=true — верим полю формата
        assert detect_remote(location="Hong Kong", workplace_type="Hybrid",
                             explicit_flag=True) is False
        assert detect_remote(location="Hong Kong", workplace_type="Remote") is True

    def test_stray_remote_word_is_not_a_signal(self):
        from src.parsers.normalize import detect_remote
        assert detect_remote(location="New York",
                             description="provide remote troubleshooting via slack") is False

    def test_explicit_remote_phrase_counts(self):
        from src.parsers.normalize import detect_remote
        assert detect_remote(location="New York",
                             description="This is a fully remote position.") is True

    def test_remote_location_counts(self):
        from src.parsers.normalize import detect_remote
        assert detect_remote(location="Remote - EMEA") is True

    def test_clean_description_limit(self):
        from src.models import MAX_DESCRIPTION_CHARS
        from src.parsers.normalize import clean_description
        assert len(clean_description("x" * 99999)) == MAX_DESCRIPTION_CHARS
        assert clean_description(None) == ""

    def test_onsite_note_added_for_hybrid(self):
        from src.parsers.normalize import onsite_note
        assert "on-site presence required" in onsite_note("Hybrid")
        assert onsite_note("Remote") == ""


class TestEnglishModality:
    """Разговорный английский кандидату недоступен (A1–A2), письменный —
    закрывается переводчиком/AI. Это разные барьеры (решение владельца 05.08.2026):
    spoken/generic режем, written пропускаем со штрафом."""

    @pytest.mark.parametrize("text", [
        "Excellent command of spoken and written English is required.",
        "Strong written and verbal English; a confident communicator.",
        "Fluent English - daily calls with international partners.",
        "Fluent English and participation in weekly meetings.",
        # «verbally and in writing» (Binance): при `verbal` вместо `verbal\w*`
        # это определялось как ПИСЬМЕННОЕ требование — найдено замером 05.08.2026
        "Strong communication skills in English verbally and in writing.",
    ])
    def test_spoken_is_hard_gate(self, text):
        from src.matcher.pre_filter import _english_modality, _fluent_english_required
        assert _english_modality(text) == "spoken"
        assert _fluent_english_required(text) is True

    @pytest.mark.parametrize("text", [
        "Must-haves: Strong written English - clear, concise, and empathetic.",
        "Excellent written English for handling support tickets.",
        "Требуется свободный английский язык в переписке.",
    ])
    def test_written_only_passes_with_penalty(self, text):
        from src.matcher.pre_filter import _english_modality, _fluent_english_required
        assert _english_modality(text) == "written"
        assert _fluent_english_required(text) is False

    @pytest.mark.parametrize("text", [
        "Fluency in English required; Greek is a strong plus.",
        "Fluent English is a must for this role.",
    ])
    def test_generic_is_hard_gate(self, text):
        from src.matcher.pre_filter import _english_modality, _fluent_english_required
        assert _english_modality(text) == "generic"
        assert _fluent_english_required(text) is True

    @pytest.mark.parametrize("text", [
        "English is a plus.",
        "Мы ищем специалиста поддержки для русскоязычных клиентов.",
    ])
    def test_no_requirement(self, text):
        from src.matcher.pre_filter import _english_modality
        assert _english_modality(text) is None

    def test_modality_read_near_requirement_not_whole_vacancy(self):
        """«Созвоны» в разделе про условия не делают письменное требование устным —
        иначе почти любая вакансия схлопнется в spoken."""
        from src.matcher.pre_filter import _english_modality
        text = ("Requirements: strong written English for support tickets. "
                + "Filler text about the product. " * 12
                + "Benefits: team calls on Fridays, video meetings, offsites.")
        assert _english_modality(text) == "written"

    def test_written_only_is_penalised_not_zeroed(self):
        from src.matcher.pre_filter import score_vacancy
        res = score_vacancy(
            "Crypto Operations Specialist",
            "Fully remote. On-chain operations, transaction monitoring, CEX/DEX, "
            "staking, custody. Strong written English for internal documentation.",
            "crypto_ops",
        )
        assert res["passed_gate"] is True
        assert res["score"] > 0
        assert any("письменный английский" in r for r in res["reasons"])
