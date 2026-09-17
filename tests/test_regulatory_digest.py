"""Дайджест регуляторики: сбор каждый день, отправка раз в неделю.

Ревизия 17.09.2026: RSS отдаёт 30 последних новостей (~1 сутки), а скрипт
запускался раз в неделю и фильтровал «за 7 дней» — то есть видел одни сутки из
семи и стабильно писал «не найдено». Плюс «сбер» ловил «сбережения», а «<» в
заголовке роняло отправку молча.
"""
import datetime as dt
import json

import pytest

from tools import regulatory_digest as rd

FEED = """<?xml version="1.0"?><rss><channel>
<item><title>{t1}</title><link>https://x/1</link><pubDate>{d}</pubDate></item>
<item><title>{t2}</title><link>https://x/2</link><pubDate>{d}</pubDate></item>
</channel></rss>"""


def _feed(t1, t2):
    when = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return FEED.format(t1=t1, t2=t2, d=when).encode("utf-8")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "digest.jsonl"
    monkeypatch.setattr(rd, "STORE", path)
    monkeypatch.setattr(rd, "FEEDS", [("Тест", "https://feed")])
    return path


class TestKeywords:
    @pytest.mark.parametrize("title", [
        "Сбербанк запустил операции с цифровыми валютами",
        "Сбер тестирует цифровой рубль",
        "ЦБ РФ обновил реестр обменников",
        "Госдума приняла закон о майнинге",
        "Неквалы получат допуск к ЦФА",
    ])
    def test_relevant_titles_match(self, title):
        assert rd.matches(title)

    @pytest.mark.parametrize("title", [
        "Как копить сбережения в кризис",          # «сбер» внутри «сбережения»
        "Bitcoin ETF approved in the US",
        "Смарфон с криптокошельком",               # «рф» внутри слова
    ])
    def test_irrelevant_titles_do_not_match(self, title):
        assert not rd.matches(title)


class TestCollect:
    def test_collects_and_deduplicates_by_link(self, store, monkeypatch):
        monkeypatch.setattr(rd, "_fetch", lambda url: _feed(
            "ЦБ РФ обновил реестр", "Погода в Самаре"))
        fresh, total = rd.collect()
        assert (fresh, total) == (1, 1), "нерелевантный заголовок не должен попадать"

        # второй сбор в тот же день: та же ссылка не дублируется
        fresh2, total2 = rd.collect()
        assert (fresh2, total2) == (0, 1)
        rows = [json.loads(ln) for ln in store.read_text(encoding="utf-8").splitlines() if ln]
        assert len(rows) == 1 and rows[0]["link"] == "https://x/1"

    def test_new_item_appends(self, store, monkeypatch):
        monkeypatch.setattr(rd, "_fetch", lambda url: _feed("Минфин о крипте", "шум"))
        rd.collect()
        monkeypatch.setattr(rd, "_fetch", lambda url: _feed(
            "Минфин о крипте", "Госдума приняла закон о майнинге"))
        fresh, total = rd.collect()
        assert (fresh, total) == (1, 2)

    def test_dead_feed_does_not_crash(self, store, monkeypatch):
        def boom(url):
            raise OSError("feed down")
        monkeypatch.setattr(rd, "_fetch", boom)
        assert rd.collect() == (0, 0)


class TestDigest:
    def _write(self, store, rows):
        store.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                         encoding="utf-8")

    def _row(self, title, link="https://x/1", days_ago=0):
        when = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=days_ago)
        return {"source": "Тест", "title": title, "link": link,
                "published": when.isoformat(), "collected_at": when.isoformat()}

    def test_week_old_items_are_included(self, store):
        """Главное: новость, собранная 5 дней назад, доживает до отправки."""
        self._write(store, [self._row("ЦБ РФ обновил реестр", days_ago=5)])
        assert "ЦБ РФ обновил реестр" in rd.build_digest()

    def test_older_than_week_dropped(self, store):
        self._write(store, [self._row("Старая новость", days_ago=9)])
        assert rd.build_digest() is None

    def test_title_and_link_are_escaped(self, store):
        self._write(store, [self._row('ЦБ <b>про "крипту"</b>', link='https://x/?a=1&b="2"')])
        out = rd.build_digest()
        assert "&lt;b&gt;" in out and "&amp;b=" in out
        assert "<b>про" not in out, "сырой тег в тексте роняет отправку (Telegram 400)"

    def test_empty_store(self, store):
        assert rd.build_digest() is None
