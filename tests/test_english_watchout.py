"""Английский в «Учесть» — только если вакансия сама о нём говорит.

Повод — 20.10.2026: карточка «Специалист технической поддержки» (ЭсТуЭс Некст,
hh 138619798) пришла с пунктом «Ограниченный уровень английского (A1-A2), но
работа предполагает русскоязычную поддержку», хотя вакансия про язык молчит.
Замер: так у 72 из 310 отправленных карточек, а видимых пунктов всего два.
"""
from unittest.mock import MagicMock, patch

from src.matcher import cerebras_matcher as m
from src.models import Job


def _job(description: str, title: str = "Специалист технической поддержки"):
    return Job(id="hh_1", title=title, company="X", description=description,
               url="https://example.com", source="hh.ru")


def _run(job: Job, watch_out: list[str]):
    import json
    raw = json.dumps([{"id": "hh_1", "score": 70, "why_fits": ["ok"],
                       "watch_out": watch_out, "recommendation": "-"}],
                     ensure_ascii=False)
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content=raw))]
    with patch.object(m, "_build_profile_text", return_value="profile"):
        out = m.match_batch([job], client=client,
                            provider=m._PROVIDER_REGISTRY["openrouter"])
    return out[0].watch_out


NOTES = [
    "Отсутствие опыта в медицинских информационных системах",
    "Ограниченный уровень английского (A1‑A2), но работа предполагает русскоязычную поддержку",
    "Нет прямого опыта в голосовых роботах",
]


class TestEnglishWatchOut:
    def test_dropped_when_listing_is_silent(self):
        out = _run(_job("Поддержка пользователей МИС, SQL, Postman, звонки"), NOTES)
        assert out == [NOTES[0], NOTES[2]]

    def test_kept_when_listing_mentions_english(self):
        out = _run(_job("Английский язык на уровне чтения документации"), NOTES)
        assert out == NOTES

    def test_kept_when_listing_mentions_english_in_latin(self):
        out = _run(_job("Requirements: English B1"), NOTES)
        assert out == NOTES

    def test_kept_when_listing_mentions_foreign_language(self):
        out = _run(_job("Знание иностранного языка будет плюсом"), NOTES)
        assert out == NOTES

    def test_other_notes_untouched(self):
        out = _run(_job("Ночные смены 2/2"), ["Ночные смены 2/2"])
        assert out == ["Ночные смены 2/2"]
