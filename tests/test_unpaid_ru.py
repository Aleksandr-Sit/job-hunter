"""Неоплачиваемая работа, заявленная по-русски, должна отсеиваться.

Инцидент 17.08.2026: вакансия «Стажер по вайбкодингу» (Jol AI) со словами
«Стажировка бесплатная и неоплачиваемая» дала НОЛЬ срабатываний стоп-листа —
он был только англоязычным. Ту вакансию спасло другое правило, но это была
удача, а не работа гейта.
"""
from src.matcher.pre_filter import CRITERIA, _hits, _n


def _stops(text: str):
    return _hits(CRITERIA["global_hard_exclude"], _n(text))[0]


class TestUnpaidRussianCaught:
    def test_real_case(self):
        assert _stops("Стажировка бесплатная и неоплачиваемая. "
                      "По окончании выдается сертификат.")

    def test_other_wordings(self):
        for s in ("Стажировка неоплачиваемая",
                  "Работа не оплачивается на период испытательного срока",
                  "Сотрудничество без оплаты труда",
                  "Ищем помощника на волонтёрских началах",
                  "Бесплатная стажировка для студентов"):
            assert _stops(s), s

    def test_english_still_caught(self):
        for s in ("This is an unpaid internship",
                  "Volunteer only position",
                  "No salary, equity only"):
            assert _stops(s), s


class TestPerksNotMistakenForUnpaid:
    """Перелов тут дороже недолова: «бесплатное обучение» — это ЛЬГОТА,
    и такие вакансии как раз целевые для кандидата."""

    def test_free_benefits_pass(self):
        for s in ("Бесплатное обучение за счёт компании",
                  "Бесплатный ДМС со стоматологией",
                  "Бесплатное питание в офисе и корпоративный транспорт",
                  "Оплачиваемая стажировка с бесплатным обучением",
                  "Предоставляем бесплатное оборудование"):
            assert not _stops(s), s

    def test_paid_internship_passes(self):
        assert not _stops("Оплачиваемая стажировка, оклад обсуждается")
