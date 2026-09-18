"""Ложные отказы предфильтра: отрицание, возраст, склейка дублей, звёздочка.

Все четыре найдены ревизией 17.09.2026 и все «тихие»: вакансия не приходит, а
причина в логе выглядит законной.
"""
from src.matcher.pre_filter import (
    _high_exp_required,
    _matches,
    _night_shift_mode,
    dedupe_jobs,
    dedupe_key,
)
from src.models import Job


class TestNightShiftNegation:
    def test_plain_night_shift_is_still_a_blocker(self):
        assert _night_shift_mode("график 2/2, ночные смены обязательны") == "core"
        assert _night_shift_mode("rotating night shift coverage") == "core"

    def test_negation_before(self):
        assert _night_shift_mode("без ночных смен, обычный график") != "core"
        assert _night_shift_mode("no night shifts, weekdays only") != "core"

    def test_negation_after(self):
        assert _night_shift_mode("ночных смен нет") != "core"
        assert _night_shift_mode("ночные смены не предусмотрены") != "core"

    def test_negation_in_one_place_does_not_whitewash_another(self):
        text = "Ночных смен нет в офисе. Удалённо — работа в ночную смену по графику."
        assert _night_shift_mode(text) == "core"

    def test_occasional_still_wins(self):
        assert _night_shift_mode("2-4 night shifts per month") == "occasional"


class TestExperienceGate:
    def test_real_requirement_still_blocks(self):
        assert _high_exp_required("требуется опыт от 7 лет в операциях")
        assert _high_exp_required("at least 8 years of experience required")

    def test_age_is_not_experience(self):
        assert not _high_exp_required("возраст от 18 лет, опыт не требуется")
        assert not _high_exp_required("требования: возраст от 20 лет и старше")

    def test_company_history_is_not_experience(self):
        assert not _high_exp_required(
            "our company has been building payments for over 15 years")
        assert not _high_exp_required("компания основана более 12 лет назад")

    def test_age_mention_does_not_hide_a_real_requirement(self):
        assert _high_exp_required(
            "Возраст от 18 лет. Опыт работы в комплаенсе не менее 8 лет.")


class TestDedupeKey:
    def test_service_brackets_still_collapse(self):
        assert dedupe_key("Ripple", "Consultant (Remote)") == dedupe_key("Ripple", "Consultant")
        assert dedupe_key("X", "Analyst (Full-time)") == dedupe_key("X", "Analyst")

    def test_meaningful_brackets_stay_different(self):
        latam = dedupe_key("Acme", "Менеджер P2P (LatAm)")
        cis = dedupe_key("Acme", "Менеджер P2P (CIS — СНГ)")
        assert latam != cis, "разные регионы — разные вакансии"

    def test_same_job_still_one_key(self):
        assert dedupe_key("Coinbase", "Risk  Analyst IV") == dedupe_key("coinbase", "Risk Analyst IV!")


class TestTelegramDedupe:
    @staticmethod
    def _job(channel, title, desc):
        return Job(id=f"{channel}-{title}-{desc[:5]}", title=title, company=channel,
                   description=desc, url="u", source=f"telegram:{channel}")

    def test_same_post_in_two_channels_collapses(self):
        text = "Ищем оператора криптообменника, удалённо, USDT"
        jobs = [self._job("@a", "Вакансия", text), self._job("@b", "Вакансия", text)]
        assert len(dedupe_jobs(jobs)) == 1

    def test_different_posts_with_same_title_survive(self):
        jobs = [self._job("@a", "Вакансия", "Ищем P2P-менеджера в Москве"),
                self._job("@b", "Вакансия", "Нужен AML-аналитик, удалённо")]
        assert len(dedupe_jobs(jobs)) == 2, "разные вакансии склеились по заголовку"


class TestStarInsideTerm:
    def test_star_in_the_middle_matches_word_forms(self):
        assert _matches("внедрени* ии", "опыт внедрения ии в процессы")
        assert _matches("внедрени* ии", "внедрение ии")

    def test_star_in_the_middle_does_not_match_anything(self):
        assert not _matches("внедрени* ии", "внедрение crm без ai")

    def test_trailing_star_unchanged(self):
        assert _matches("операц*", "операционные процессы")
        assert not _matches("операц*", "кооперация")
