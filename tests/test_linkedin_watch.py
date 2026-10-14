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
        p.delay = p.search_delay = 0
        p.max_minutes = 45
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
        p.pages_per_query, p.hours_old, p.delay, p.search_delay = 1, 168, 0, 0
        p.max_minutes = 45
        p.max_descriptions, p._strikes = 0, 0
        p.watch_companies = [{"name": "AMLBot", "id": "64276802"}]
        called = []
        monkeypatch.setattr(p, "_company_cards",
                            lambda cid, s: called.append(cid) or [])
        p.parse()
        assert called == ["64276802"]


class TestPublicSearchPage:
    """С 13.10.2026 поиск идёт через публичную страницу /jobs/search: guest-поиск
    ушёл в 429, а публичная страница отдаёт до 60 карточек за один запрос."""

    def _p(self):
        p = _parser()
        p.hours_old, p.pages_per_query, p.delay, p._strikes = 168, 3, 0, 0
        p.search_delay = 0
        return p

    def test_first_page_goes_to_public_search(self):
        p = self._p()
        urls = []
        p._get = lambda url: urls.append(url) or "<title>433 Russian jobs in Cyprus</title>" + CARD
        cards = p._cards('"Russian speaking"', "106774002", 0)
        assert len(cards) == 1
        assert urls[0].startswith("https://www.linkedin.com/jobs/search?")
        assert "seeMoreJobPostings" not in urls[0]
        assert len(urls) == 1

    def test_valid_empty_page_does_not_fall_back(self):
        """Компания без вакансий (AMLBot) — честно пустая страница. Лишний запрос к
        guest-поиску — ровно к тому адресу, что ловит 429."""
        p = self._p()
        urls = []
        p._get = lambda url: urls.append(url) or "<title>Jobs Jobs at AMLBot in Worldwide | LinkedIn</title>"
        assert p._company_cards("64276802", 0) == []
        assert len(urls) == 1

    def test_network_error_does_not_fall_back(self):
        p = self._p()
        urls = []
        p._get = lambda url: urls.append(url) or ""
        assert p._cards("q", "1", 0) == []
        assert len(urls) == 1

    def test_login_wall_falls_back_to_guest_paging(self):
        p = self._p()
        urls = []

        def fake(url):
            urls.append(url)
            return "<title>LinkedIn Login, Sign in</title>" if "/jobs/search?" in url else CARD
        p._get = fake
        cards = p._cards("q", "1", 0)
        assert len(cards) == 1
        assert "seeMoreJobPostings" in urls[1]


class TestSkipSeen:
    def test_seen_cards_get_no_description(self, monkeypatch):
        from src import storage
        p = _parser()
        monkeypatch.setattr(storage, "is_seen_batch", lambda ids, prefilter_version=None: {"li_1"})
        cards = [{"id": "1"}, {"id": "2"}]
        assert [c["id"] for c in p._unseen(cards)] == ["2"]

    def test_batch_dump_mode_keeps_everything(self, monkeypatch):
        """tools/diag/dump_batch.py снимает ВЕСЬ поток для замеров criteria-ab."""
        from src import storage
        p = _parser()
        p.skip_seen = False
        monkeypatch.setattr(storage, "is_seen_batch", lambda ids, prefilter_version=None: {"li_1"})
        assert len(p._unseen([{"id": "1"}, {"id": "2"}])) == 2

    def test_db_error_keeps_everything(self, monkeypatch):
        from src import storage
        p = _parser()

        def boom(ids, prefilter_version=None):
            raise RuntimeError("no db")
        monkeypatch.setattr(storage, "is_seen_batch", boom)
        assert len(p._unseen([{"id": "1"}])) == 1


class TestStageDeadline:
    def test_blocked_after_deadline(self, monkeypatch):
        """Редкий успех сбрасывает счётчик 429 подряд — без потолка времени парсер
        не сдаётся никогда (замер 14.10.2026: описания 4,5 часа)."""
        import time as _t
        p = _parser()
        p._strikes, p.max_minutes = 0, 45
        p._deadline = _t.monotonic() - 1
        assert p._blocked() is True

    def test_not_blocked_before_deadline(self):
        import time as _t
        p = _parser()
        p._strikes, p.max_minutes = 0, 45
        p._deadline = _t.monotonic() + 600
        assert p._blocked() is False
