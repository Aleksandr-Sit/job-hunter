"""Уровень языка, написанный кириллицей, должен ловиться так же, как латиницей.

Инцидент 17.08.2026: вакансия Synergy of Lake Technology «Customer support manager
(Remote, Crypto)» требовала «Английский С1 и выше» — и прошла гейт с 66 баллами.
Буква С в «С1» оказалась кириллической (U+0421), а шаблон ждал латинскую C.

Это самая частая форма в русскоязычных вакансиях: слово «Английский» набирают
русской раскладкой и не переключаются на уровне.
"""
from src.matcher.pre_filter import _english_modality, _fluent_english_required, _n


class TestHomoglyphNormalisation:
    def test_cyrillic_letter_before_digit_becomes_latin(self):
        assert _n("Английский С1 и выше") == "английский c1 и выше"
        assert _n("английский В2") == "английский b2"
        assert _n("уровень А2") == "уровень a2"

    def test_russian_words_are_not_damaged(self):
        # Сплошная замена кириллицы сломала бы русский текст и русские шаблоны.
        for s in ("распознавание скам-схем", "свободный английский",
                  "аналитика данных", "выводы средств", "вакансия"):
            assert _n(s) == s.lower(), s

    def test_letter_with_space_before_digit_untouched(self):
        # «с 2 до 5», «в 1 час» — предлог с числом, не уровень языка.
        assert _n("работа с 2 до 5") == "работа с 2 до 5"
        assert _n("в 1 смену") == "в 1 смену"

    def test_long_numbers_untouched(self):
        assert _n("артикул с12345") == "артикул с12345"


class TestGateSeesCyrillicLevel:
    """Главное — что уровень вообще ОПОЗНАЁТСЯ. Резать его или штрафовать —
    отдельное решение (см. test_level_c_and_window.py): с 17.08.2026 C1/C2 без
    пометки «устный» даёт штраф, а не отсев."""

    def test_c1_cyrillic_is_recognised(self):
        # Дословные строки из вакансии, на которой баг и нашёлся.
        for s in ("Английский С1 и выше.",
                  "Английский язык — С1",
                  "английский: С2",
                  "Английский уровня С1"):
            assert _english_modality(_n(s)) == "level_c", s

    def test_c1_latin_recognised_the_same(self):
        assert _english_modality(_n("Английский C1")) == "level_c"
        assert _english_modality(_n("English at C1 level or above.")) == "level_c"

    def test_cyrillic_c1_with_spoken_marker_is_gated(self):
        # Устный английский закрыт наглухо — кириллица тут ничего не меняет.
        assert _fluent_english_required(_n("Английский С1, устный и письменный."))

    def test_b_level_cyrillic_is_penalty_not_gate(self):
        # B1-B2 — штраф, а не отсев (решение владельца 14.08.2026).
        for s in ("Английский В2", "английский язык — В1", "Английский от В2"):
            blob = _n(s)
            assert _english_modality(blob) == "level_b", f"{s} -> {_english_modality(blob)}"
            assert not _fluent_english_required(blob), s


class TestNoOverreach:
    def test_a_level_is_not_a_barrier(self):
        # У кандидата A1-A2 — своё же требование резать нельзя.
        assert not _fluent_english_required(_n("Английский А2 достаточно"))

    def test_plain_text_without_english_untouched(self):
        assert not _fluent_english_required(
            _n("Работа с 2 до 5, вахта в 1 смену, оклад от 2 до 3 тысяч."))
