"""C1/C2 без пометки «устный» — штраф, а не отсев. И окно модальности не должно
залезать в соседнее требование.

Оба правила родились из одной вакансии (Synergy of Lake Technology, 17.08.2026):
поддержка там прямым текстом текстовая (email, чаты, соцсети), английский заявлен
как «Английский С1 и выше» без уточнения модальности — но гейт назвал его
разговорным, потому что в окно ±90 символов попало «отличные навыки ведения
переговоров» из ОТДЕЛЬНОГО пункта двумя строками выше.
"""
from src.matcher.pre_filter import (
    _english_modality,
    _fluent_english_required,
    _n,
    _requirement_window,
    score_vacancy,
)


class TestRequirementWindow:
    def test_window_stops_at_bullet_break(self):
        blob = ("отличные навыки ведения переговоров. "
                "умение действовать без инструкции. "
                "английский c1 и выше. общая техническая грамотность.")
        i = blob.index("английский")
        w = _requirement_window(blob, i, i + len("английский c1"))
        assert "переговоров" not in w
        assert "инструкции" not in w
        assert "английский c1" in w

    def test_window_keeps_same_sentence(self):
        blob = "требуется свободный разговорный английский для созвонов с клиентами."
        i = blob.index("английский")
        w = _requirement_window(blob, i, i + len("английский"))
        assert "созвонов" in w


class TestLevelC:
    DESC = ("поддержка клиентов в текстовом формате через email и чаты. "
            "английский c1 и выше. общая техническая грамотность.")

    def test_c_level_without_spoken_is_penalty(self):
        blob = _n(self.DESC)
        assert _english_modality(blob) == "level_c"
        assert not _fluent_english_required(blob)

    def test_cyrillic_c_level_same(self):
        blob = _n("Поддержка в чатах. Английский С1 и выше. Техническая грамотность.")
        assert _english_modality(blob) == "level_c"
        assert not _fluent_english_required(blob)

    def test_explicit_spoken_c_level_is_still_gated(self):
        # Устный английский закрыт наглухо — уровень тут ничего не меняет.
        for s in ("отличный английский c1, устный и письменный.",
                  "english c1, spoken and written, daily calls."):
            blob = _n(s)
            assert _english_modality(blob) == "spoken", s
            assert _fluent_english_required(blob), s

    def test_penalty_lowers_score_but_lets_through(self):
        res = score_vacancy(
            "Customer support manager",
            "Поддержка клиентов в текстовом формате через email, чаты и соцсети. "
            "Опыт работы с криптовалютами и биржами. Английский С1 и выше. Удалённо.",
            "web3_support")
        assert res["passed_gate"]
        assert any("C1" in r for r in res["reasons"]), res["reasons"]

    def test_b_level_still_wins_over_c_ordering(self):
        # B и C в одной вакансии: строже C, его и возвращаем.
        blob = _n("английский b2 обязателен. для менеджеров английский c1.")
        assert _english_modality(blob) == "level_c"


class TestNoRegression:
    def test_generic_fluent_still_gated(self):
        assert _fluent_english_required(_n("Fluent English is a must for this role."))

    def test_written_only_still_penalty(self):
        blob = _n("Must-haves: strong written english - clear and concise.")
        assert _english_modality(blob) == "written"
        assert not _fluent_english_required(blob)

    def test_english_as_plus_still_passes(self):
        assert not _fluent_english_required(_n("English is a plus."))
