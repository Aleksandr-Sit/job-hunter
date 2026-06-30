"""
Batch matcher на базе Cerebras (бесплатно без карты, OpenAI-совместимый).
Модель: llama-3.3-70b — та же Llama 3.3 70B, 30 req/min бесплатно.
Ключ: inference.cerebras.ai → Sign up → Get API key.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

from openai import OpenAI

from ..models import Job, MatchResult
from .. import storage

logger = logging.getLogger(__name__)


class _AIGeoBlockError(Exception):
    """Raised when AI provider returns 403 — stops processing all remaining batches."""


_PROFILE_DIR = Path(__file__).parent.parent.parent / "config" / "profile"
_MATCHES_JSONL = Path(__file__).parent.parent.parent / "data" / "matches.jsonl"
_BATCH_SIZE = 5
_CEREBRAS_TIMEOUT = 30      # секунды ожидания ответа API
_CEREBRAS_MAX_RETRY = 3     # попытки при 429/5xx
_CEREBRAS_RETRY_SLEEP = 20  # секунды между попытками

_SYSTEM_INSTRUCTION = """\
You are a job matching assistant for a specific candidate (full profile below).
Candidate: ~5 years of hands-on crypto/web3 on-chain experience (operations,
wallets, exchanges, multichain), background in support/sales. English level
A1–A2 (basic). Wants remote work or relocation (Cyprus/Greece/Thailand/Turkey/
Armenia/UAE/Serbia).

Since June 2026 the candidate has been building hands-on AI-automation skills
with Claude Code (~1 month at time of writing) and has two working portfolio
projects to show as proof of skill, not just claimed knowledge: a job-search
automation pipeline (Python, AI-matching, Docker, multi-source parsing) and a
multi-bot crypto trading system (Python, multi-exchange, Docker, VPS,
watchdog/risk-management architecture, currently in paper-trading mode). When
matching AI-automation roles, weigh these concrete projects as real evidence
of ability even though the candidate has no paid AI-automation work history —
but he IS a genuine beginner, so do not expect him to fit roles demanding
years of professional ML/software-engineering experience.

Three priority roles — score against whichever fits best:
1. Crypto/Web3/DeFi Operations (primary, strongest fit — direct hands-on experience)
2. Web3/Crypto Support (secondary — English is the bottleneck here)
3. AI Automation / no-code / workflow automation (any industry, not limited to
   crypto/web3) — entry-level fit, backed by the two portfolio projects above

Evaluate each job listing against the candidate profile and score the fit from 0 to 100.

Scoring guide:
- 90–100: perfect match — role, domain, skills, format all align, no strong English required
- 75–89: strong match with 1–2 minor gaps
- 65–74: decent match worth considering
- 50–64: partial match, notable gaps
- 0–49: poor fit

Score down for: requiring fluent/native English or C1/C2 (penalize harder for
Support roles than Operations); voice/phone support or call center; sales
quotas/cold calling/upsell; leadership titles (Head/Director/Lead/VP/Chief);
office-only in a location outside Russia/Cyprus/Greece/Thailand/Turkey/Armenia/
UAE/Serbia; for AI-automation roles, requiring years of professional ML/SWE
experience, a CS degree, or research-scientist-level depth.

Score 0 if: purely a development role (Solidity/Rust/Smart-contract/Software
Engineer — unless it's the AI-automation role and the "development" is
light scripting/no-code glue work like n8n/Zapier/Python automation, which is
in scope), unpaid/volunteer/equity-only, scam signals (pay-to-apply, send
funds), or not relevant to any of the three roles at all.

Russian-speaking / CIS team or community is a clear plus — boost the score,
especially for Support roles.

IMPORTANT: Write ALL text fields (why_fits, watch_out, recommendation) in RUSSIAN language only. No English in these fields.

Respond ONLY with a valid JSON array, no markdown, no extra text:
[
  {
    "id": "job_id",
    "score": 85,
    "why_fits": ["причина на русском", "ещё причина"],
    "watch_out": ["нюанс на русском"],
    "recommendation": "Одно конкретное действие при отклике — на русском"
  }
]"""


def _compact_resume(text: str) -> str:
    """Оставляет только релевантные секции резюме — убирает личные данные и retail-опыт."""
    keep = {"профессиональный профиль", "ключевые навыки", "языки", "сильные стороны"}
    result, skip, in_exp, in_web3 = [], True, False, False
    for line in text.split("\n"):
        ll = line.lower().strip()
        if line.startswith("## "):
            in_exp = "опыт работы" in ll
            in_web3 = False
            skip = not (any(s in ll for s in keep) or in_exp)
        elif line.startswith("### ") and in_exp:
            in_web3 = "independent web3" in ll or "web3" in ll
            skip = not in_web3
        if not skip:
            result.append(line)
    return "\n".join(result).strip()


@lru_cache(maxsize=1)
def _build_profile_text() -> str:
    resume_full = (_PROFILE_DIR / "resume.md").read_text(encoding="utf-8")
    skills = (_PROFILE_DIR / "skills.json").read_text(encoding="utf-8")
    prefs_raw = json.loads((_PROFILE_DIR / "preferences.json").read_text(encoding="utf-8"))

    # Только поля, нужные для матчинга (убираем locations_ok, employment_type и т.д.)
    prefs_compact = {k: prefs_raw[k] for k in (
        "roles", "salary", "tech_stack_must", "tech_stack_nice",
        "experience_level", "english_level", "notes",
    ) if k in prefs_raw}

    return (
        f"# PROFILE\n{_compact_resume(resume_full)}\n\n"
        f"# SKILLS\n{skills}\n\n"
        f"# PREFERENCES\n{json.dumps(prefs_compact, ensure_ascii=False, indent=2)}"
    )


def _detect_local_proxy(host: str = "127.0.0.1", port: int = 10808) -> str | None:
    """Проверяет доступность локального прокси и возвращает URL если есть."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=1):
            return f"http://{host}:{port}"
    except OSError:
        return None


def _get_client() -> OpenAI:
    api_key = os.environ.get("CEREBRAS_API_KEY")
    if not api_key:
        raise ValueError("CEREBRAS_API_KEY not set in .env")

    proxy_url = _detect_local_proxy()
    kwargs: dict = {
        "api_key": api_key,
        "base_url": "https://api.cerebras.ai/v1",
    }
    if proxy_url:
        import httpx
        logger.debug("Cerebras: routing via proxy %s", proxy_url)
        kwargs["http_client"] = httpx.Client(proxy=proxy_url, timeout=_CEREBRAS_TIMEOUT)
    return OpenAI(**kwargs)


def match_batch(jobs: list[Job], client: Optional[OpenAI] = None) -> list[MatchResult]:
    if not jobs:
        return []

    if client is None:
        client = _get_client()

    model_name = os.environ.get("CEREBRAS_MODEL", "llama-3.3-70b")
    profile_text = _build_profile_text()
    jobs_text = "\n\n---\n\n".join(f"JOB ID: {j.id}\n{j.to_text()}" for j in jobs)

    prompt = (
        f"CANDIDATE PROFILE:\n{profile_text}\n\n"
        f"===\n\nEvaluate these {len(jobs)} job listings:\n\n{jobs_text}"
    )

    raw = None
    for attempt in range(1, _CEREBRAS_MAX_RETRY + 1):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": _SYSTEM_INSTRUCTION},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=4096,
                timeout=_CEREBRAS_TIMEOUT,
            )
            raw = response.choices[0].message.content.strip()
            break
        except Exception as e:
            err_str = str(e)
            is_geo_block = "403" in err_str or "access denied" in err_str.lower() or "unauthorized" in err_str.lower()
            is_rate_limit = "429" in err_str or "rate_limit" in err_str.lower()
            is_server_err = "5" in err_str[:3] or "503" in err_str or "502" in err_str
            if is_geo_block:
                logger.error("Cerebras blocked (403). Check CEREBRAS_API_KEY. Skipping all batches.")
                raise _AIGeoBlockError() from e
            if (is_rate_limit or is_server_err) and attempt < _CEREBRAS_MAX_RETRY:
                wait = _CEREBRAS_RETRY_SLEEP * attempt
                logger.warning("Cerebras attempt %d/%d failed (%s). Retrying in %ds…",
                               attempt, _CEREBRAS_MAX_RETRY, e, wait)
                time.sleep(wait)
            else:
                logger.error("Cerebras error (attempt %d): %s", attempt, e)
                return []

    if raw is None:
        return []

    start = raw.find("[")
    end = raw.rfind("]") + 1
    if start == -1 or end == 0:
        logger.error("Cerebras returned non-JSON: %s", raw[:300])
        return []

    try:
        data = json.loads(raw[start:end])
    except json.JSONDecodeError as e:
        logger.error("JSON parse error: %s | raw: %s", e, raw[:300])
        return []

    results = []
    for item in data:
        try:
            results.append(MatchResult(
                job_id=str(item["id"]),
                score=int(item["score"]),
                why_fits=item.get("why_fits", []),
                watch_out=item.get("watch_out", []),
                recommendation=item.get("recommendation", ""),
            ))
        except (KeyError, ValueError) as e:
            logger.warning("Skipping malformed match item: %s", e)

    logger.info("Batch matched %d jobs via Cerebras", len(results))
    return results


def match_jobs(jobs: list[Job], threshold: int = 65, batch_size: int = _BATCH_SIZE) -> list[tuple[Job, MatchResult]]:
    """Матчит все вакансии батчами, возвращает только те что >= threshold."""
    client = _get_client()

    to_match: list[Job] = []
    cached_results: list[tuple[Job, MatchResult]] = []

    for job in jobs:
        cached = storage.get_cached_match(job.id)
        if cached:
            if cached.score >= threshold:
                cached_results.append((job, cached))
        else:
            to_match.append(job)

    _MATCHES_JSONL.parent.mkdir(parents=True, exist_ok=True)

    fresh_results: list[tuple[Job, MatchResult]] = []
    total_batches = (len(to_match) + batch_size - 1) // batch_size
    for i in range(0, len(to_match), batch_size):
        batch_idx = i // batch_size + 1
        if i > 0:
            time.sleep(5)
        batch = to_match[i : i + batch_size]
        batch_map = {j.id: j for j in batch}
        try:
            results = match_batch(batch, client)
        except _AIGeoBlockError:
            break  # 403 — прекращаем все батчи, не тратим время
        with _MATCHES_JSONL.open("a", encoding="utf-8") as f:
            for r in results:
                storage.save_match(r)
                f.write(json.dumps({
                    "job_id": r.job_id, "score": r.score,
                    "why_fits": r.why_fits, "watch_out": r.watch_out,
                    "recommendation": r.recommendation,
                    "batch": batch_idx,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False) + "\n")
                if r.score >= threshold:
                    job = batch_map.get(r.job_id)
                    if job:
                        fresh_results.append((job, r))
            # чекпоинт батча — маркер завершения
            f.write(json.dumps({
                "_checkpoint": True,
                "batch": batch_idx,
                "of": total_batches,
                "count": len(results),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False) + "\n")
        logger.info("Checkpoint: batch %d/%d saved to matches.jsonl", batch_idx, total_batches)

    all_results = cached_results + fresh_results
    all_results.sort(key=lambda x: x[1].score, reverse=True)
    return all_results


# ── тест ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env")

    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()

    if args.test:
        storage.init_db()

        test_jobs = [
            Job(
                id="test-1",
                title="Web3 QA Tester",
                company="DeFi Protocol",
                description="Looking for a Web3 QA specialist to test our DeFi platform. "
                            "Experience with wallets, dApps, on-chain transactions required. "
                            "Remote. $2000–3500/mo.",
                url="https://example.com/job/1",
                source="test",
                salary_min=2000, salary_max=3500, is_remote=True,
                published_at=datetime.now(timezone.utc),
            ),
            Job(
                id="test-2",
                title="Python Backend Developer",
                company="SaaS Company",
                description="Django, PostgreSQL, AWS. Office in Moscow. No crypto.",
                url="https://example.com/job/2",
                source="test",
                is_remote=False,
                published_at=datetime.now(timezone.utc),
            ),
            Job(
                id="test-3",
                title="Crypto Operations Specialist",
                company="Exchange",
                description="Manage crypto operations, monitor transactions, work with CEX/DEX. "
                            "Experience with Binance, OKX required. Full remote. $1800–2500/mo.",
                url="https://example.com/job/3",
                source="test",
                salary_min=1800, salary_max=2500, is_remote=True,
                published_at=datetime.now(timezone.utc),
            ),
        ]

        print("Testing Cerebras matcher with 3 jobs...\n")
        results = match_jobs(test_jobs, threshold=0)
        for job, match in results:
            print(f"[{match.score}/100] {job.title}")
            print(f"  Why fits: {match.why_fits}")
            print(f"  Watch out: {match.watch_out}")
            print(f"  {match.recommendation}\n")
