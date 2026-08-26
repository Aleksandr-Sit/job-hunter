"""Кривой JSON от модели не должен терять весь батч.

Повод — 26.08.2026: OpenRouter вернул ответ, где ВНУТРИ строки стоял сырой
управляющий символ вместо экранированного. Строгий json.loads упал, батч из
5 вакансий остался неоценённым, а запасной провайдер в этот момент был мёртв
(Cerebras, 402 с 18.08). Замер по логам: 2 таких случая на 37 прогонов.

Здесь зафиксировано: управляющий символ в строке разбирается нестрого и батч
доезжает, но НАСТОЯЩАЯ поломка формата по-прежнему честно возвращает None.
"""
from unittest.mock import MagicMock, patch

from src.matcher import cerebras_matcher as m
from src.models import Job


def _job(jid="hh_1"):
    return Job(id=jid, title="AI Automation Engineer", company="X",
               description="Автоматизация процессов, n8n, LLM",
               url="https://example.com", source="hh.ru")


def _client_returning(raw: str):
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content=raw))
    ]
    return client


def _run(raw: str):
    with patch.object(m, "_build_profile_text", return_value="profile"):
        return m.match_batch([_job()], client=_client_returning(raw),
                             provider=m._PROVIDER_REGISTRY["cerebras"])


class TestControlCharacterInJson:
    def test_raw_newline_inside_string_is_recovered(self):
        """Сырой перевод строки внутри значения — батч всё равно разбирается."""
        raw = '[{"id": "hh_1", "score": 70, "why_fits": ["Опыт\nWeb3"], "why_not": []}]'
        out = _run(raw)
        assert out is not None, "батч потерян из-за управляющего символа"
        assert len(out) == 1
        assert out[0].job_id == "hh_1"
        assert out[0].score == 70

    def test_raw_tab_inside_string_is_recovered(self):
        raw = '[{"id": "hh_1", "score": 61, "why_fits": ["Docker\tVPS"], "why_not": []}]'
        out = _run(raw)
        assert out is not None and out[0].score == 61

    def test_valid_json_still_parses(self):
        raw = '[{"id": "hh_1", "score": 55, "why_fits": ["ok"], "why_not": []}]'
        out = _run(raw)
        assert out is not None and out[0].score == 55

    def test_genuinely_broken_json_still_returns_none(self):
        """Нестрогий разбор НЕ должен маскировать реальную поломку структуры."""
        raw = '[{"id": "hh_1", "score": 70, "why_fits": [oops}]'
        assert _run(raw) is None

    def test_non_json_answer_still_returns_none(self):
        assert _run("Извините, не могу оценить эти вакансии.") is None
