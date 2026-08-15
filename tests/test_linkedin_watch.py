"""Слежка за конкретными работодателями в LinkedIn.

Мотив: у AMLBot и Match Systems вакансии появляются редко, обычный поиск по
ключевым словам и странам их не ловит, а у Match Systems карьерной страницы нет
вообще (все типовые пути отдают 404, проверено 15.08.2026).

Механика проверена живым запросом: `f_C` сам по себе гостевым эндпоинтом не
принимается — нужен ещё geoId. С `geoId=92000000` фильтр отдаёт ровно вакансии
указанной компании.
"""
from src.parsers.web.linkedin import _WORLDWIDE_GEO, LinkedInParser

CARD = (
    '<ul><li>'
    '<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/'
    'aml-analyst-at-amlbot-4460000001?position=1">'
    '<h3 class="base-search-card__title"> AML Analyst </h3>'
    '<h4 class="base-search-card__subtitle"><a href="#">AMLBot</a></h4>'
    '<span class="job-search-card__location"> Remote </span></a>'
    '</li></ul>'
)


def _parser():
    return LinkedInParser.__new__(LinkedInParser)


class TestCompanyCards:
    def test_url_carries_f_C_and_worldwide_geo(self):
        p = _parser()
        seen = {}
        p._get = lambda url: seen.setdefault("url", url) and ""
        p._company_cards("64276802", 0)
        assert "f_C=64276802" in seen["url"]
        assert f"geoId={_WORLDWIDE_GEO}" in seen["url"]

    def test_no_freshness_filter(self):
        # f_TPR обрезал бы выдачу неделей — для редко публикующихся компаний это
        # означало бы пропустить открытие вакансии.
        p = _parser()
        seen = {}
        p._get = lambda url: seen.setdefault("url", url) and ""
        p._company_cards("81872165", 0)
        assert "f_TPR" not in seen["url"]

    def test_cards_parsed_same_as_geo_search(self):
        p = _parser()
        p._get = lambda url: CARD
        cards = p._company_cards("64276802", 0)
        assert len(cards) == 1
        assert cards[0]["id"] == "4460000001"
        assert cards[0]["company"] == "AMLBot"
        assert cards[0]["title"] == "AML Analyst"

    def test_empty_response_means_no_openings(self):
        p = _parser()
        p._get = lambda url: ""
        assert p._company_cards("64276802", 0) == []


class TestWatchOrdering:
    def test_watched_companies_are_queued_first(self, monkeypatch):
        """Описания качаются в порядке вставки и обрываются на max_descriptions.
        Если watch-компания окажется в хвосте, её вакансия не доедет до скоринга."""
        p = _parser()
        p.enabled = True
        p.queries = ["support"]
        p.geos = [{"id": "106774002", "name": "Кипр"}]
        p.pages_per_query = 1
        p.hours_old = 168
        p.delay = 0
        p.max_descriptions = 0        # описания не качаем — проверяем только порядок
        p._strikes = 0
        p.watch_companies = [{"name": "AMLBot", "id": "64276802"}]

        order = []
        monkeypatch.setattr(p, "_company_cards",
                            lambda cid, s: order.append("watch") or [])
        monkeypatch.setattr(p, "_cards",
                            lambda q, g, s: order.append("geo") or [])
        p.parse()
        assert order and order[0] == "watch"

    def test_watch_alone_is_enough_to_run(self, monkeypatch):
        p = _parser()
        p.enabled = True
        p.queries, p.geos = [], []
        p.pages_per_query, p.hours_old, p.delay = 1, 168, 0
        p.max_descriptions, p._strikes = 0, 0
        p.watch_companies = [{"name": "AMLBot", "id": "64276802"}]
        called = []
        monkeypatch.setattr(p, "_company_cards",
                            lambda cid, s: called.append(cid) or [])
        p.parse()
        assert called == ["64276802"]
