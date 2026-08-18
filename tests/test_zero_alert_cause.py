"""Алерт «вакансий не отправлено» обязан отличать сбой AI от низких оценок.

Повод — 18.08.2026: у Cerebras кончилась квота, все 4 батча упали с 402,
ни одна вакансия не была оценена. В Telegram при этом ушло «Причина: AI оценил
ниже порога». Александр прочитал это как «был мусор» и не стал разбираться —
то есть ложная причина стоила суток простоя. Проверка чинит именно это.
"""
from unittest.mock import patch

from src.matcher import cerebras_matcher
from src.scheduler import _send_zero_alert


def _alert(**stats) -> str:
    """Возвращает текст, который ушёл бы в Telegram."""
    cerebras_matcher.last_run_stats.update(
        {"batches": 0, "failed_batches": 0, "unscored": 0} | stats
    )
    with patch("src.scheduler.send_text") as send:
        _send_zero_alert(total=3590, unseen=555, prefiltered=16, matched=0, threshold=55)
    return send.call_args[0][0]


class TestCause:
    def test_all_batches_failed_reports_ai_down(self):
        # Ровно ситуация 18.08.2026: 4 батча, 16 вакансий, ни одной оценки.
        text = _alert(batches=4, failed_batches=4, unscored=16)
        assert "AI недоступен" in text
        assert "ниже порога" not in text      # главное: ложной причины больше нет
        assert "16" in text
        assert "не потеряны" in text

    def test_partial_failure_mentions_both(self):
        # Часть оценена честно, часть нет — молчать о второй половине нельзя.
        text = _alert(batches=4, failed_batches=1, unscored=5)
        assert "ниже порога" in text
        assert "1 из 4" in text

    def test_healthy_run_keeps_old_wording(self):
        # Без сбоев формулировка прежняя — регрессии в обычном случае нет.
        text = _alert(batches=4, failed_batches=0, unscored=0)
        assert text.rstrip().endswith("Причина: AI оценил ниже порога")
        assert "AI недоступен" not in text

    def test_prefilter_zero_wins_over_stale_stats(self):
        # prefiltered==0 → причина в предфильтре, даже если в счётчике мусор
        # с прошлого прогона. Иначе алерт обвинит AI, который не запускался.
        cerebras_matcher.last_run_stats.update(batches=9, failed_batches=9, unscored=45)
        with patch("src.scheduler.send_text") as send:
            _send_zero_alert(total=3590, unseen=555, prefiltered=0, matched=0, threshold=55)
        assert "pre-filter" in send.call_args[0][0]
