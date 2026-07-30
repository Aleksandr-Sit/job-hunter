"""Тесты хранилища: версионный seen-гейт и трекер откликов.

Версионный seen (PREFILTER_AUDIT §5.3) — механизм, который переоткрывает
отказы pre-filter при смене критериев. Ломается молча, поэтому под тестом.
"""
import pytest

from src import storage
from src.models import Job


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Изолированная БД на каждый тест — боевая не трогается."""
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")
    storage.init_db()
    return storage


def _job(jid: str) -> Job:
    return Job(id=jid, title=f"Job {jid}", company="ACME",
               description="desc", url=f"http://x/{jid}", source="test")


class TestVersionedSeen:
    def test_verdict_is_seen_forever(self, db):
        db.mark_seen_batch([_job("a")])
        assert db.is_seen_batch(["a"], prefilter_version="v1") == {"a"}
        # даже при другой версии — вердикт остаётся seen
        assert db.is_seen_batch(["a"], prefilter_version="v2") == {"a"}

    def test_prefilter_reject_reopens_on_new_version(self, db):
        db.mark_prefilter_seen([_job("b")], "v1")
        assert db.is_seen_batch(["b"], prefilter_version="v1") == {"b"}
        # смена критериев → отказ устарел → вакансия снова в воронке
        assert db.is_seen_batch(["b"], prefilter_version="v2") == set()

    def test_reject_upgrades_to_verdict(self, db):
        db.mark_prefilter_seen([_job("c")], "v1")
        db.mark_seen_batch([_job("c")])          # дошла до AI → вердикт
        assert db.is_seen_batch(["c"], prefilter_version="v2") == {"c"}

    def test_verdict_not_downgraded_by_reject(self, db):
        db.mark_seen_batch([_job("d")])
        db.mark_prefilter_seen([_job("d")], "v9")
        assert db.is_seen_batch(["d"], prefilter_version="other") == {"d"}

    def test_empty_input(self, db):
        assert db.is_seen_batch([], prefilter_version="v1") == set()


class TestApplications:
    def test_save_and_list(self, db):
        assert db.save_application("j1", "Ops Specialist", "Binance", "http://u", 87)
        rows = db.list_applications()
        assert len(rows) == 1
        assert rows[0]["title"] == "Ops Specialist"
        assert rows[0]["score"] == 87
        assert rows[0]["status"] == "applied"

    def test_duplicate_is_rejected(self, db):
        assert db.save_application("j1", "T", "C") is True
        assert db.save_application("j1", "T", "C") is False
        assert len(db.list_applications()) == 1

    def test_stats(self, db):
        db.save_application("j1", "A", "C1")
        db.save_application("j2", "B", "C2")
        stats = db.application_stats()
        assert stats["total"] == 2
        assert stats["last7"] == 2
        assert stats["stale"] == 0

    def test_empty_stats(self, db):
        assert db.application_stats()["total"] == 0
