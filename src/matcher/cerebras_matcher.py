"""
Batch matcher поверх любого OpenAI-совместимого провайдера.

Провайдеров можно перечислить несколько: при отказе одного (кончилась квота,
отозван ключ, гео-блок) матчер сам переходит к следующему. Повод — 18.08.2026:
у Cerebras кончилась квота, провайдер был единственный, и бот двое суток не
оценивал вакансии.

Порядок берётся из AI_PROVIDER_ORDER (через запятую), по умолчанию
"openrouter,cerebras". Провайдер участвует, только если задан его ключ,
поэтому лишние можно просто не прописывать в .env.
"""
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

from openai import OpenAI

from .. import storage
from ..models import Job, MatchResult

logger = logging.getLogger(__name__)


class _ProviderDeadError(Exception):
    """Провайдер отказал целиком (401/402/403), а не на одном батче.

    Означает «переключись на следующего», а не «прекрати работу»: остальные
    провайдеры могут быть живы.
    """


@dataclass(frozen=True)
class Provider:
    """OpenAI-совместимый эндпоинт. Ключ и модель читаются из окружения."""
    name: str
    base_url: str
    key_env: str
    model_env: str
    default_model: str

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.key_env)

    @property
    def model(self) -> str:
        return os.environ.get(self.model_env) or self.default_model


# Реестр. Добавить нового — одна строка здесь + ключ в .env, код не трогается.
_PROVIDER_REGISTRY: dict[str, Provider] = {
    # gpt-oss-120b — та же модель, что крутилась на Cerebras: поведение скоринга
    # не меняется, кэш прежних оценок остаётся сопоставимым. Через OpenRouter
    # она вдесятеро дешевле ($0.03/$0.17 против $0.35/$0.75 за 1M токенов).
    "openrouter": Provider("openrouter", "https://openrouter.ai/api/v1",
                           "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
                           "openai/gpt-oss-120b"),
    "cerebras":   Provider("cerebras", "https://api.cerebras.ai/v1",
                           "CEREBRAS_API_KEY", "CEREBRAS_MODEL",
                           "gpt-oss-120b"),
    "groq":       Provider("groq", "https://api.groq.com/openai/v1",
                           "GROQ_API_KEY", "GROQ_MODEL",
                           "llama-3.3-70b-versatile"),
    "deepseek":   Provider("deepseek", "https://api.deepseek.com/v1",
                           "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL",
                           "deepseek-chat"),
    "proxyapi":   Provider("proxyapi", "https://api.proxyapi.ru/openai/v1",
                           "PROXYAPI_API_KEY", "PROXYAPI_MODEL",
                           "gpt-4o-mini"),
}

_DEFAULT_ORDER = "openrouter,cerebras"


def available_providers() -> list[Provider]:
    """Провайдеры по порядку из AI_PROVIDER_ORDER, у которых задан ключ."""
    order = os.environ.get("AI_PROVIDER_ORDER", _DEFAULT_ORDER)
    out: list[Provider] = []
    for raw in order.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        p = _PROVIDER_REGISTRY.get(name)
        if p is None:
            logger.warning("AI_PROVIDER_ORDER: неизвестный провайдер %r — пропущен", name)
            continue
        if not p.api_key:
            logger.debug("Провайдер %s пропущен: %s не задан", name, p.key_env)
            continue
        out.append(p)
    return out


_PROFILE_DIR = Path(__file__).parent.parent.parent / "config" / "profile"
_MATCHES_JSONL = Path(__file__).parent.parent.parent / "data" / "matches.jsonl"
_BATCH_SIZE = 5

# Итог последнего вызова match_jobs. Нужен алерту «ноль вакансий»: без него
# сбой провайдера (402/403) неотличим от «AI оценил ниже порога», и алерт
# называет ложную причину — 18.08.2026 квота Cerebras кончилась, а в Telegram
# ушло «AI оценил ниже порога».
last_run_stats: dict[str, object] = {
    "batches": 0, "failed_batches": 0, "unscored": 0,
    "provider": "",   # кто в итоге оценил
    "switched": 0,    # 1, если пришлось переключаться на запасного
}

_CEREBRAS_TIMEOUT = 30      # секунды ожидания ответа API
_CEREBRAS_MAX_RETRY = 3     # попытки при 429/5xx
_CEREBRAS_RETRY_SLEEP = 20  # секунды между попытками

_SYSTEM_INSTRUCTION = """\
You are a job matching assistant for a specific candidate (full profile below).
Candidate: ~6 years of hands-on crypto/web3 on-chain experience (operations,
wallets, exchanges, multichain), background in support/sales. English level
A1–A2 (basic). Wants remote work or relocation (Cyprus/Greece/Thailand/Turkey/
Armenia/UAE/Serbia).

Since June 2026 the candidate has been building hands-on AI-automation skills
with Claude Code and now has FIVE working open-source portfolio projects as
proof of skill, not just claimed knowledge: (1) a job-search automation pipeline
(Python, AI-matching, Docker, multi-source parsing); (2) a multi-bot crypto
trading system (Python, multi-exchange, Docker, VPS, watchdog/risk-management,
paper-trading mode); (3) an on-chain analytics pipeline for Solana wallet
discovery (Dune data via DuckDB); (4) a crypto accumulation-zone scanner
(multi-stage funnel, anti-rug/honeypot filters, backtests); (5) a Telegram AI
assistant (aiogram, APScheduler, SQLite, pluggable LLM provider, unit-tested
deterministic core). When matching AI-automation roles, weigh these concrete
projects as real evidence of ability even though the candidate has no paid
AI-automation work history — but he IS a genuine beginner, so do not expect him
to fit roles demanding years of professional ML/software-engineering experience.

IMPORTANT — the candidate ALSO has 8+ years of documented SALES and customer
service experience (retail and telecom), and it is his longest verified track
record: consistently top-3 seller at his location, recognised among the
company's 200 best sellers nationwide, sent by management to ramp up newly
opened stores, sales plan always met and regularly exceeded. Treat sales as a
REAL target direction, not as a downside.

SEVEN target directions — score against whichever fits best:
1. Crypto/Web3/DeFi Operations (primary, strongest fit — direct hands-on experience)
2. Web3/Crypto Support (English is the bottleneck here)
3. Customer/Technical Support in fintech, payments, IT or SaaS — backed by 8+
   years of client-facing work; Russian-speaking desks are ideal
4. AML / KYC / transaction monitoring / compliance — backed by practical P2P,
   fiat on/off-ramp and transaction-verification experience (NOTE: no formal AML
   employment and no CAMS certificate — entry level, not senior/MLRO roles)
5. AI Automation / no-code / workflow automation (any industry, not limited to
   crypto/web3) — entry-level fit, backed by the five portfolio projects above
6. Web3 QA / manual testing (manual QA, NOT SDET/automation) — adjacent to the
   candidate's hands-on habit of breaking wallets/dApps/transactions; entry-level,
   written bug reports (English A2 is enough)
7. Remote SALES / account management on INBOUND leads — best in crypto, fintech,
   payments, B2B or e-commerce, where his 8+ years of sales plus crypto domain
   knowledge is a rare combination. Inbound flow, warm leads, account management
   and upselling to existing clients are all GOOD fits.

Evaluate each job listing against the candidate profile and score the fit from 0 to 100.

Scoring guide:
- 90–100: perfect match — role, domain, skills, format all align, no strong English required
- 75–89: strong match with 1–2 minor gaps
- 65–74: decent match worth considering
- 50–64: partial match, notable gaps
- 0–49: poor fit

Score down for: requiring fluent/native English or C1/C2 (penalize harder for
Support roles than Operations); voice/phone support IN ENGLISH or call-centre
work; COLD calling, cold outbound prospecting, "active client search", field
visits and heavy travel; leadership titles (Head/Director/Lead/VP/Chief/MLRO);
office-only in a location outside Russia/Cyprus/Greece/Thailand/Turkey/Armenia/
UAE/Serbia; for AI-automation roles, requiring years of professional ML/SWE
experience, a CS degree, or research-scientist-level depth.

NIGHT SHIFTS: if the listing mentions night shifts, overnight work, a 24/7 duty
rota or a 2/2 twelve-hour schedule, you MUST say so explicitly in watch_out and
cut the score hard — even "2-4 night shifts per month" is a serious drawback for
this candidate. A schedule built around night or evening shifts is close to a
dealbreaker: score it below 50. He must never learn about night shifts only
after opening the listing.

Do NOT penalise a job merely for being a sales role. Penalise only the cold
outbound part described above. Sales on inbound leads, account management,
customer success and upselling to existing clients match the candidate's
strongest verified experience — score those on their merits.

Score 0 if: purely a development role (Solidity/Rust/Smart-contract/Software
Engineer — unless it's the AI-automation role and the "development" is
light scripting/no-code glue work like n8n/Zapier/Python automation, which is
in scope), unpaid/volunteer/equity-only, scam signals (pay-to-apply, send
funds), or not relevant to any of the seven target directions at all.

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
    """Профиль для промпта: убираем только ЛИЧНЫЕ ДАННЫЕ (контакты).

    Раньше отсюда вырезался ВЕСЬ опыт найма, кроме Web3-блока («убирает
    retail-опыт») — это было логично, пока целью была одна крипто-роль.
    Сейчас направлений семь, и четыре из них (продажи, поддержка в финтех/IT,
    AML, крипто-поддержка) опираются именно на розницу и телеком. Из-за среза
    модель не видела 8+ лет продаж и поддержки и ставила таким вакансиям 0
    с формулировкой «не имеет отношения» (замер 05.08.2026).

    Контакты убираем осознанно: для матчинга бесполезны, а в промпт лишние
    персональные данные слать не нужно.
    """
    drop_sections = ("личная информация", "контакты")
    result, skip = [], False
    for line in text.split("\n"):
        if line.startswith("## "):
            skip = any(s in line.lower() for s in drop_sections)
        if not skip:
            result.append(line)
    return "\n".join(result).strip()


_CRITERIA_FILE = Path(__file__).parent.parent.parent / "config" / "criteria.yaml"


@lru_cache(maxsize=1)
def _scoring_version() -> str:
    """Отпечаток промпта+профиля+критериев. Меняется — кэш баллов инвалидируется."""
    import hashlib
    try:
        criteria = _CRITERIA_FILE.read_text(encoding="utf-8")
    except OSError:
        criteria = ""
    blob = _SYSTEM_INSTRUCTION + _build_profile_text() + criteria
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:12]


def _read_profile_file(name: str) -> str:
    """Читает файл профиля; в свежем клоне/CI личного файла нет → берём `.example`.
    Профиль в .gitignore (личные данные), поэтому отсутствие — штатная ситуация."""
    for candidate in (name, f"{name}.example"):
        path = _PROFILE_DIR / candidate
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    raise FileNotFoundError(
        f"Нет {name} в config/profile/ (ни личного, ни .example). "
        "Скопируй .example и заполни своими данными."
    )


@lru_cache(maxsize=1)
def _build_profile_text() -> str:
    resume_full = _read_profile_file("resume.md")
    skills = _read_profile_file("skills.json")
    prefs_raw = json.loads(_read_profile_file("preferences.json"))

    # Только поля, нужные для матчинга (убираем locations_ok, employment_type и т.д.).
    # «schedule» добавлен 19.08.2026: без него модель не знала про ночные смены и
    # ставила вакансии с ними 70+, не упоминая график даже в watch_out. Добавляя
    # сюда новый ключ, помни: этот текст входит в _scoring_version.
    prefs_compact = {k: prefs_raw[k] for k in (
        "roles", "salary", "tech_stack_must", "tech_stack_nice",
        "experience_level", "english_level", "schedule", "notes",
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


def _get_client(provider: Optional[Provider] = None) -> OpenAI:
    """Клиент для провайдера. Без аргумента — первый доступный по порядку."""
    if provider is None:
        providers = available_providers()
        if not providers:
            raise ValueError(
                "Не задан ни один ключ AI-провайдера. Пропиши в .env хотя бы "
                "OPENROUTER_API_KEY или CEREBRAS_API_KEY."
            )
        provider = providers[0]

    if not provider.api_key:
        raise ValueError(f"{provider.key_env} not set in .env")

    proxy_url = _detect_local_proxy()
    kwargs: dict = {"api_key": provider.api_key, "base_url": provider.base_url}
    if proxy_url:
        import httpx
        logger.debug("%s: routing via proxy %s", provider.name, proxy_url)
        kwargs["http_client"] = httpx.Client(proxy=proxy_url, timeout=_CEREBRAS_TIMEOUT)
    return OpenAI(**kwargs)


def _is_provider_dead(err: str) -> bool:
    """Отказ провайдера целиком, а не сбой одного батча.

    402 — кончилась квота (Cerebras, 18.08.2026), 401 — ключ отозван,
    403 — гео-блок. Повторять запрос к этому же провайдеру бессмысленно:
    следующий батч упрётся в то же самое.
    """
    low = err.lower()
    return (
        "402" in err or "payment_required" in low or "insufficient" in low
        or "401" in err or "unauthorized" in low or "invalid_api_key" in low
        or "403" in err or "access denied" in low
    )


def match_batch(jobs: list[Job], client: Optional[OpenAI] = None,
                provider: Optional[Provider] = None) -> Optional[list[MatchResult]]:
    """Возвращает список результатов, либо None при сбое API/парсинга ответа.

    None означает «батч НЕ оценён» — вызывающий не должен считать это отказом.
    _ProviderDeadError означает «этот провайдер отказал целиком» — вызывающий
    должен переключиться на следующего, а не бросать батч."""
    if not jobs:
        return []

    if provider is None:
        providers = available_providers()
        provider = providers[0] if providers else _PROVIDER_REGISTRY["cerebras"]
    if client is None:
        client = _get_client(provider)

    model_name = provider.model
    profile_text = _build_profile_text()
    # schedule_note читает ПОЛНОЕ описание: to_text() отдаёт только начало и конец,
    # а требование про смены обычно стоит ровно в выброшенной середине.
    from ..parsers.normalize import schedule_note
    jobs_text = "\n\n---\n\n".join(
        f"JOB ID: {j.id}\n{j.to_text()}{schedule_note(j.description)}" for j in jobs)

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
            if _is_provider_dead(err_str):
                logger.error("Провайдер %s отказал целиком: %s", provider.name, err_str[:200])
                raise _ProviderDeadError(provider.name) from e
            is_rate_limit = "429" in err_str or "rate_limit" in err_str.lower()
            is_server_err = any(c in err_str for c in ("500", "502", "503", "504"))
            if (is_rate_limit or is_server_err) and attempt < _CEREBRAS_MAX_RETRY:
                wait = _CEREBRAS_RETRY_SLEEP * attempt
                logger.warning("%s: попытка %d/%d не удалась (%s). Повтор через %dс…",
                               provider.name, attempt, _CEREBRAS_MAX_RETRY, e, wait)
                time.sleep(wait)
            else:
                logger.error("%s: ошибка (попытка %d): %s", provider.name, attempt, e)
                return None

    if raw is None:
        return None

    start = raw.find("[")
    end = raw.rfind("]") + 1
    if start == -1 or end == 0:
        logger.error("Cerebras returned non-JSON: %s", raw[:300])
        return None

    try:
        data = json.loads(raw[start:end])
    except json.JSONDecodeError as e:
        # Модель изредка вставляет ВНУТРЬ строки сырой управляющий символ
        # (перевод строки, таб) вместо экранированного, и строгий разбор роняет
        # весь батч. Замер по логам 26.08.2026: 2 случая на 37 прогонов (~5%),
        # и один из них совпал с мёртвым запасным провайдером — 5 вакансий
        # уехали на следующий прогон. `strict=False` разрешает управляющие
        # символы в строках, структуру JSON при этом не ослабляет: настоящая
        # поломка формата по-прежнему не разберётся и уйдёт в ERROR ниже.
        # Нестрогий разбор — ТОЛЬКО после отказа строгого, чтобы факт кривого
        # ответа модели оставался видимым в логе, а не растворялся молча.
        try:
            data = json.loads(raw[start:end], strict=False)
            logger.warning(
                "JSON от модели содержал управляющий символ, разобран нестрого: %s", e)
        except json.JSONDecodeError:
            logger.error("JSON parse error: %s | raw: %s", e, raw[:300])
            return None

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

    logger.info("Батч оценён: %d вакансий через %s", len(results), provider.name)
    return results


def match_jobs(jobs: list[Job], threshold: int = 65, batch_size: int = _BATCH_SIZE) -> list[tuple[Job, MatchResult]]:
    """Матчит все вакансии батчами, возвращает только те что >= threshold.

    Помечает вакансии seen только ПОСЛЕ вердикта (кэш или успешный батч):
    сбойный батч остаётся неотмеченным и вернётся на следующем прогоне.

    Провайдеров перебирает по порядку: отказавший (402/401/403) исключается
    до конца прогона, батч тут же повторяется на следующем."""
    providers = available_providers()
    if not providers:
        raise ValueError(
            "Не задан ни один ключ AI-провайдера. Пропиши в .env хотя бы "
            "OPENROUTER_API_KEY или CEREBRAS_API_KEY."
        )
    logger.info("AI-провайдеры: %s", ", ".join(f"{p.name}({p.model})" for p in providers))
    clients: dict[str, OpenAI] = {}
    version = _scoring_version()

    to_match: list[Job] = []
    cached_jobs: list[Job] = []
    cached_results: list[tuple[Job, MatchResult]] = []

    for job in jobs:
        cached = storage.get_cached_match(job.id, version)
        if cached:
            cached_jobs.append(job)
            if cached.score >= threshold:
                cached_results.append((job, cached))
        else:
            to_match.append(job)

    # у кэшированных вердикт уже есть
    storage.mark_seen_batch(cached_jobs)

    _MATCHES_JSONL.parent.mkdir(parents=True, exist_ok=True)

    fresh_results: list[tuple[Job, MatchResult]] = []
    total_batches = (len(to_match) + batch_size - 1) // batch_size
    last_run_stats.update(batches=total_batches, failed_batches=0, unscored=0,
                          provider="", switched=0)
    dead: set[str] = set()

    def _score_batch(batch: list[Job]) -> Optional[list[MatchResult]]:
        """Пробует батч на живых провайдерах по порядку.

        Отказавший целиком помечается мёртвым до конца прогона — иначе каждый
        следующий батч заново упирался бы в ту же пустую квоту.
        """
        for p in providers:
            if p.name in dead:
                continue
            try:
                if p.name not in clients:
                    clients[p.name] = _get_client(p)
                out = match_batch(batch, clients[p.name], p)
            except _ProviderDeadError:
                dead.add(p.name)
                logger.warning("Провайдер %s исключён до конца прогона", p.name)
                last_run_stats["switched"] = 1
                continue
            if out is not None:
                last_run_stats["provider"] = p.name
                return out
            # None — разовый сбой (таймаут, битый JSON). Пробуем следующего.
        return None

    for i in range(0, len(to_match), batch_size):
        batch_idx = i // batch_size + 1
        if i > 0:
            time.sleep(5)
        batch = to_match[i : i + batch_size]
        batch_map = {j.id: j for j in batch}
        results = _score_batch(batch)
        if results is None:
            logger.error("Батч %d/%d не оценён — %d вакансий остались непросмотренными, "
                         "вернутся в следующий прогон", batch_idx, total_batches, len(batch))
            last_run_stats["failed_batches"] += 1
            last_run_stats["unscored"] += len(batch)
            continue
        storage.mark_seen_batch(batch)
        with _MATCHES_JSONL.open("a", encoding="utf-8") as f:
            for r in results:
                storage.save_match(r, version)
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


# ── Генератор сопроводительных писем ──────────────────────────────────────────

_LETTER_INSTRUCTION = """\
You write SHORT job application cover letters for a specific candidate.

Rules:
- 4-6 sentences MAX. Recruiters skim; long letters lose.
- Language: write in RUSSIAN if the company/role is Russian-speaking (Russian
  title, RU company, CIS team). Otherwise write in ENGLISH, but keep it SIMPLE
  (the candidate's English is A2 — the letter must sound like something he could
  plausibly write and defend in a text conversation; no fancy idioms).
- Structure: (1) which role and why you're writing; (2) 2-3 CONCRETE facts from
  the profile that match this job; (3) one sentence on availability/format
  (remote); (4) short closing.
- Use ONLY facts from the candidate profile below. NEVER invent employers,
  certificates, degrees or years of experience.
- CRITICAL — never merge the two separate experience tracks:
  (a) 6 years of Web3/DeFi = the candidate's OWN on-chain operations as a
      self-employed operator (wallets, exchanges, transactions, P2P). It is
      NOT customer-facing work. Never write that he "supported users" or
      "handled requests" during this period.
  (b) 8+ years of customer/technical support and sales = employed at retail and
      telecom companies (M.Video-Eldorado, MegaFon Retail, ATRI). This is where
      client requests, SLA and troubleshooting come from.
  Attributing (b) duties to period (a) is a factual error that collapses in an
  interview. Keep them clearly separate.
- The candidate has NO formal AML/KYC job and no CAMS certificate. He passed
  verifications as a platform USER, assessed P2P counterparties and monitored
  HIS OWN transactions. Never write that he "ensured compliance", "controlled
  AML/KYC requirements" or "performed KYC checks" for a company. Phrase it as
  practical experience ("monitored transactions", "went through verification
  procedures", "assessed counterparties in P2P deals").
- No flattery, no "I am passionate about your mission", no buzzwords.
- Do not mention salary. Do not apologise for missing skills.
- Output ONLY the letter text — no subject line, no markdown, no commentary."""


def generate_cover_letter(title: str, company: str, why_fits: list[str],
                          recommendation: str = "") -> Optional[str]:
    """Короткое сопроводительное под конкретную вакансию. None при сбое API.

    Использует уже посчитанные AI-причины совпадения (why_fits) — они извлечены
    из описания вакансии на этапе матчинга, поэтому письмо получается предметным
    без повторного хранения полного текста вакансии.
    """
    providers = available_providers()
    if not providers:
        logger.error("Сопроводительное: не задан ни один ключ AI-провайдера")
        return None

    facts = "\n".join(f"- {r}" for r in (why_fits or [])[:5])
    prompt = (
        f"CANDIDATE PROFILE:\n{_build_profile_text()}\n\n===\n\n"
        f"JOB:\nTitle: {title}\nCompany: {company}\n"
        f"Why the matcher considered it a fit:\n{facts}\n"
        f"{('Advice for applying: ' + recommendation) if recommendation else ''}\n\n"
        "Write the cover letter now."
    )
    # Письмо генерируется по нажатию кнопки в Telegram: если первый провайдер
    # отказал, молча вернуть None — значит показать пользователю пустоту.
    for p in providers:
        try:
            resp = _get_client(p).chat.completions.create(
                model=p.model,
                messages=[
                    {"role": "system", "content": _LETTER_INSTRUCTION},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=600,
                timeout=_CEREBRAS_TIMEOUT,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text
        except Exception as e:
            logger.error("Сопроводительное: %s не справился — %s", p.name, str(e)[:200])
    return None
