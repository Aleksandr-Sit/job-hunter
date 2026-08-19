"""Отказ одного AI-провайдера не должен останавливать прогон.

Повод — 18.08.2026: у Cerebras кончилась квота, провайдер был единственный,
и бот двое суток не оценивал вакансии, сообщая при этом «AI оценил ниже порога».
Здесь зафиксировано: 402/401/403 означают «смени провайдера», а не «сдайся»,
и отказавший не опрашивается повторно на каждом следующем батче.
"""
import os
from unittest.mock import patch

import pytest

from src.matcher import cerebras_matcher as m


class TestProviderRegistry:
    def test_only_providers_with_keys_are_used(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER_ORDER", "openrouter,cerebras,groq")
        monkeypatch.setenv("OPENROUTER_API_KEY", "x")
        monkeypatch.setenv("CEREBRAS_API_KEY", "y")
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        assert [p.name for p in m.available_providers()] == ["openrouter", "cerebras"]

    def test_order_is_honoured(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER_ORDER", "cerebras,openrouter")
        monkeypatch.setenv("OPENROUTER_API_KEY", "x")
        monkeypatch.setenv("CEREBRAS_API_KEY", "y")
        assert [p.name for p in m.available_providers()] == ["cerebras", "openrouter"]

    def test_unknown_name_is_skipped_not_fatal(self, monkeypatch):
        # Опечатка в .env не должна ронять прогон целиком.
        monkeypatch.setenv("AI_PROVIDER_ORDER", "openrouter,opnerouter")
        monkeypatch.setenv("OPENROUTER_API_KEY", "x")
        assert [p.name for p in m.available_providers()] == ["openrouter"]

    def test_model_comes_from_env_with_default(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "x")
        monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
        p = m._PROVIDER_REGISTRY["openrouter"]
        assert p.model == "openai/gpt-oss-120b"
        monkeypatch.setenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")
        assert p.model == "meta-llama/llama-3.3-70b-instruct"


class TestDeadDetection:
    @pytest.mark.parametrize("err", [
        "Error code: 402 - {'type': 'payment_required_error', 'param': 'quota'}",
        "Error code: 401 - unauthorized",
        "Error code: 403 - access denied",
        "insufficient_quota",
    ])
    def test_hard_failures_mean_switch(self, err):
        assert m._is_provider_dead(err) is True

    @pytest.mark.parametrize("err", [
        "Error code: 429 - rate_limit_exceeded",
        "Error code: 503 - service unavailable",
        "Read timed out",
    ])
    def test_transient_failures_do_not_kill_provider(self, err):
        # 429 и 5xx лечатся повтором на том же провайдере — переключаться рано.
        assert m._is_provider_dead(err) is False


class TestFallbackLoop:
    def _jobs(self, n=3):
        from src.models import Job
        return [Job(id=f"j{i}", title="Support", company="Acme", url=f"u{i}",
                    description="desc", source="test") for i in range(n)]

    def test_second_provider_scores_when_first_is_out_of_quota(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER_ORDER", "cerebras,openrouter")
        monkeypatch.setenv("CEREBRAS_API_KEY", "dead")
        monkeypatch.setenv("OPENROUTER_API_KEY", "live")
        jobs = self._jobs(3)
        calls = []

        def fake_batch(batch, client=None, provider=None):
            calls.append(provider.name)
            if provider.name == "cerebras":
                raise m._ProviderDeadError("cerebras")
            from src.models import MatchResult
            return [MatchResult(job_id=j.id, score=70, why_fits=[], watch_out=[],
                                recommendation="") for j in batch]

        with patch.object(m, "match_batch", side_effect=fake_batch), \
             patch.object(m, "_get_client"), \
             patch.object(m.storage, "get_cached_match", return_value=None), \
             patch.object(m.storage, "mark_seen_batch"), \
             patch.object(m.storage, "save_match"), \
             patch.object(m, "_MATCHES_JSONL", m.Path(os.devnull)):
            out = m.match_jobs(jobs, threshold=55, batch_size=3)

        assert len(out) == 3, "запасной провайдер обязан был оценить батч"
        assert m.last_run_stats["provider"] == "openrouter"
        assert m.last_run_stats["switched"] == 1
        assert m.last_run_stats["failed_batches"] == 0

    def test_dead_provider_is_not_retried_on_later_batches(self, monkeypatch):
        # Главное про экономию времени: мёртвого спрашивают ОДИН раз за прогон,
        # а не на каждом батче — иначе прогон встанет на таймаутах.
        monkeypatch.setenv("AI_PROVIDER_ORDER", "cerebras,openrouter")
        monkeypatch.setenv("CEREBRAS_API_KEY", "dead")
        monkeypatch.setenv("OPENROUTER_API_KEY", "live")
        jobs = self._jobs(6)
        calls = []

        def fake_batch(batch, client=None, provider=None):
            calls.append(provider.name)
            if provider.name == "cerebras":
                raise m._ProviderDeadError("cerebras")
            from src.models import MatchResult
            return [MatchResult(job_id=j.id, score=70, why_fits=[], watch_out=[],
                                recommendation="") for j in batch]

        with patch.object(m, "match_batch", side_effect=fake_batch), \
             patch.object(m, "_get_client"), \
             patch.object(m, "time"), \
             patch.object(m.storage, "get_cached_match", return_value=None), \
             patch.object(m.storage, "mark_seen_batch"), \
             patch.object(m.storage, "save_match"), \
             patch.object(m, "_MATCHES_JSONL", m.Path(os.devnull)):
            m.match_jobs(jobs, threshold=55, batch_size=3)

        assert calls.count("cerebras") == 1, f"мёртвого опрашивали повторно: {calls}"
        assert calls.count("openrouter") == 2

    def test_all_providers_dead_loses_no_vacancies(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER_ORDER", "cerebras,openrouter")
        monkeypatch.setenv("CEREBRAS_API_KEY", "dead")
        monkeypatch.setenv("OPENROUTER_API_KEY", "dead")
        jobs = self._jobs(3)
        marked = []

        def fake_batch(batch, client=None, provider=None):
            raise m._ProviderDeadError(provider.name)

        with patch.object(m, "match_batch", side_effect=fake_batch), \
             patch.object(m, "_get_client"), \
             patch.object(m.storage, "get_cached_match", return_value=None), \
             patch.object(m.storage, "mark_seen_batch", side_effect=lambda b: marked.extend(b)), \
             patch.object(m, "_MATCHES_JSONL", m.Path(os.devnull)):
            out = m.match_jobs(jobs, threshold=55, batch_size=3)

        assert out == []
        assert marked == [], "непросмотренные не должны помечаться — они вернутся"
        assert m.last_run_stats["unscored"] == 3

    def test_no_keys_at_all_is_a_clear_error(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER_ORDER", "openrouter,cerebras")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            m.match_jobs(self._jobs(1), threshold=55)
