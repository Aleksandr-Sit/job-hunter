"""
Batch matcher: оценивает вакансии батчами через Claude с prompt caching профиля.
Один вызов API — до 10 вакансий. Профиль кэшируется между запросами.
"""
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import anthropic

from ..models import Job, MatchResult
from .. import storage

logger = logging.getLogger(__name__)

_PROFILE_DIR = Path(__file__).parent.parent.parent / "config" / "profile"
_BATCH_SIZE = 10


def _build_profile_text() -> str:
    resume = (_PROFILE_DIR / "resume.md").read_text(encoding="utf-8")
    skills = (_PROFILE_DIR / "skills.json").read_text(encoding="utf-8")
    prefs = (_PROFILE_DIR / "preferences.json").read_text(encoding="utf-8")
    return f"# RESUME\n{resume}\n\n# SKILLS\n{skills}\n\n# PREFERENCES\n{prefs}"


def _make_system_prompt() -> list[dict]:
    profile_text = _build_profile_text()
    return [
        {
            "type": "text",
            "text": (
                "You are a job matching assistant. You receive a candidate profile and a batch "
                "of job listings. For each job, score how well it matches the candidate on a "
                "0–100 scale. Be honest and precise.\n\n"
                "Scoring guide:\n"
                "- 90–100: perfect match (stack, role, salary, format all match)\n"
                "- 75–89: strong match with minor gaps\n"
                "- 65–74: decent match, worth considering\n"
                "- 50–64: partial match, significant gaps\n"
                "- 0–49: poor match\n\n"
                "Respond ONLY with valid JSON array, no extra text:\n"
                '[\n  {\n    "id": "job_id",\n    "score": 85,\n'
                '    "why_fits": ["reason 1", "reason 2"],\n'
                '    "watch_out": ["gap 1"],\n'
                '    "recommendation": "One sentence recommendation"\n'
                "  }\n]\n\n"
                "CANDIDATE PROFILE:"
            ),
        },
        {
            "type": "text",
            "text": profile_text,
            # Кэшируем профиль — он одинаков для всех запросов
            "cache_control": {"type": "ephemeral"},
        },
    ]


def match_batch(jobs: list[Job], client: Optional[anthropic.Anthropic] = None) -> list[MatchResult]:
    if not jobs:
        return []

    if client is None:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    jobs_text = "\n\n---\n\n".join(
        f"JOB ID: {j.id}\n{j.to_text()}" for j in jobs
    )

    try:
        response = client.messages.create(
            model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=4096,
            system=_make_system_prompt(),
            messages=[
                {
                    "role": "user",
                    "content": f"Evaluate these {len(jobs)} job listings:\n\n{jobs_text}",
                }
            ],
        )
    except anthropic.APIError as e:
        logger.error("Claude API error: %s", e)
        return []

    raw = response.content[0].text.strip()

    # Извлекаем JSON даже если модель добавила текст вокруг
    start = raw.find("[")
    end = raw.rfind("]") + 1
    if start == -1 or end == 0:
        logger.error("Claude returned non-JSON: %s", raw[:200])
        return []

    try:
        data = json.loads(raw[start:end])
    except json.JSONDecodeError as e:
        logger.error("JSON parse error: %s | raw: %s", e, raw[:200])
        return []

    results = []
    for item in data:
        try:
            r = MatchResult(
                job_id=str(item["id"]),
                score=int(item["score"]),
                why_fits=item.get("why_fits", []),
                watch_out=item.get("watch_out", []),
                recommendation=item.get("recommendation", ""),
            )
            results.append(r)
        except (KeyError, ValueError) as e:
            logger.warning("Skipping malformed match item: %s", e)

    logger.info(
        "Batch matched %d jobs. Cache usage: input=%s, cache_read=%s",
        len(results),
        response.usage.input_tokens,
        getattr(response.usage, "cache_read_input_tokens", "n/a"),
    )
    return results


def match_jobs(jobs: list[Job], threshold: int = 65) -> list[tuple[Job, MatchResult]]:
    """Матчит все вакансии батчами, возвращает только те что >= threshold."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Фильтруем уже закэшированные
    to_match: list[Job] = []
    cached_results: list[tuple[Job, MatchResult]] = []

    for job in jobs:
        cached = storage.get_cached_match(job.id)
        if cached:
            if cached.score >= threshold:
                cached_results.append((job, cached))
        else:
            to_match.append(job)

    # Батчинг
    fresh_results: list[tuple[Job, MatchResult]] = []
    for i in range(0, len(to_match), _BATCH_SIZE):
        batch = to_match[i : i + _BATCH_SIZE]
        batch_map = {j.id: j for j in batch}
        results = match_batch(batch, client)
        for r in results:
            storage.save_match(r)
            if r.score >= threshold:
                job = batch_map.get(r.job_id)
                if job:
                    fresh_results.append((job, r))

    all_results = cached_results + fresh_results
    all_results.sort(key=lambda x: x[1].score, reverse=True)
    return all_results


# ── тест ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    from datetime import datetime

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    if args.test:
        test_jobs = [
            Job(
                id="test-1",
                title="Senior Python Developer",
                company="CryptoStartup",
                description="We need a Python expert for our DeFi platform. FastAPI, PostgreSQL, Docker required. Remote. $5000–8000/mo.",
                url="https://example.com/job/1",
                source="test",
                salary_min=5000,
                salary_max=8000,
                is_remote=True,
                published_at=datetime.utcnow(),
            ),
            Job(
                id="test-2",
                title="Frontend React Developer",
                company="Agency",
                description="React.js, TypeScript, CSS. Office in Moscow.",
                url="https://example.com/job/2",
                source="test",
                is_remote=False,
                published_at=datetime.utcnow(),
            ),
            Job(
                id="test-3",
                title="Blockchain Python Engineer",
                company="Web3 Protocol",
                description="Build smart contract integrations in Python. Web3.py, Solidity knowledge is a plus. Fully remote.",
                url="https://example.com/job/3",
                source="test",
                is_remote=True,
                published_at=datetime.utcnow(),
            ),
        ]

        print("Running matcher test with 3 jobs...\n")
        results = match_jobs(test_jobs, threshold=0)
        for job, match in results:
            print(f"[{match.score}/100] {job.title}")
            print(f"  Why fits: {match.why_fits}")
            print(f"  Watch out: {match.watch_out}")
            print(f"  {match.recommendation}\n")
