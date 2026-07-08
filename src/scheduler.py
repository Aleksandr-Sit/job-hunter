"""
Основной цикл: парсинг → фильтрация → матчинг → уведомление.
Запускается по расписанию через APScheduler.
"""
import logging
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

from .models import Job
from . import storage
from .matcher.pre_filter import score_job, _prefilter_version
from .matcher.cerebras_matcher import match_jobs
from .bot.notifier import send_jobs_batch, send_daily_summary, send_text
from .bot.callback_handler import run_listener

load_dotenv(Path(__file__).parent.parent / ".env")

_LOG_FILE = Path(__file__).parent.parent / "data" / "logs" / "job-hunter.log"
_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

_CONFIG = Path(__file__).parent.parent / "config" / "settings.yaml"


def _load_config() -> dict:
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))


def _build_parsers(cfg: dict) -> list:
    from .parsers.hh_parser import HHParser
    from .parsers.telegram_parser import TelegramParser
    from .parsers.web.remoteok import RemoteOKParser
    from .parsers.web.cryptojoblist import CryptoJobListParser
    from .parsers.web.web3career import Web3CareerParser
    from .parsers.web.laborx import LaborXParser
    from .parsers.web.remote3 import Remote3Parser
    from .parsers.web.wellfound import WellFoundParser
    from .parsers.web.contra import ContraParser
    from .parsers.web.ashby import AshbyParser
    from .parsers.web.greenhouse import GreenhouseParser
    from .parsers.web.lever import LeverParser
    from .parsers.web.linkedin import LinkedInParser

    parsers_cfg = cfg.get("parsers", {})
    parsers = []

    if parsers_cfg.get("hh", {}).get("enabled", True):
        parsers.append(HHParser())
    if parsers_cfg.get("remoteok", {}).get("enabled", True):
        parsers.append(RemoteOKParser())
    if parsers_cfg.get("cryptojoblist", {}).get("enabled", True):
        parsers.append(CryptoJobListParser())
    if parsers_cfg.get("web3career", {}).get("enabled", True):
        parsers.append(Web3CareerParser())
    if parsers_cfg.get("laborx", {}).get("enabled", True):
        parsers.append(LaborXParser())
    if parsers_cfg.get("remote3", {}).get("enabled", True):
        parsers.append(Remote3Parser())
    if parsers_cfg.get("wellfound", {}).get("enabled", False):
        parsers.append(WellFoundParser())
    if parsers_cfg.get("contra", {}).get("enabled", False):
        parsers.append(ContraParser())
    if parsers_cfg.get("ashby", {}).get("enabled", True):
        parsers.append(AshbyParser())
    if parsers_cfg.get("greenhouse", {}).get("enabled", True):
        parsers.append(GreenhouseParser())
    if parsers_cfg.get("lever", {}).get("enabled", True):
        parsers.append(LeverParser())
    if parsers_cfg.get("linkedin", {}).get("enabled", False):
        parsers.append(LinkedInParser())
    if parsers_cfg.get("telegram", {}).get("enabled", True):
        parsers.append(TelegramParser())

    return parsers


def _send_zero_alert(total: int, unseen: int, prefiltered: int, matched: int, threshold: int) -> None:
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = [f"🔍 <b>Прогон {ts} — вакансий не отправлено</b>"]
    lines.append(f"Собрано: {total} | Новых: {unseen} | Pre-filter: {prefiltered} | AI ≥{threshold}%: {matched}")
    if unseen == 0:
        lines.append("Причина: все уже видели раньше (дедуп)")
    elif prefiltered == 0:
        lines.append("Причина: ни одна не прошла pre-filter (роль/домен/стоп-слова)")
    else:
        lines.append("Причина: AI оценил ниже порога")
    send_text("\n".join(lines))


def run_once() -> None:
    cfg = _load_config()
    matching_cfg = cfg.get("matching", {})
    threshold = matching_cfg.get("threshold", 65)
    batch_size = matching_cfg.get("batch_size", 5)

    storage.init_db()
    parsers = _build_parsers(cfg)

    # 1. Параллельный парсинг
    all_jobs: list[Job] = []
    active_sources: list[str] = []

    def _run_parser(parser):
        try:
            return parser.name, parser.parse(), None
        except Exception as e:
            return parser.name, [], e

    with ThreadPoolExecutor(max_workers=len(parsers)) as pool:
        futures = {pool.submit(_run_parser, p): p for p in parsers}
        for future in as_completed(futures):
            name, jobs, err = future.result()
            if err:
                logger.error("Parser %s failed: %s", name, err)
            else:
                logger.info("%s: fetched %d jobs", name, len(jobs))
                all_jobs.extend(jobs)
                if jobs:
                    active_sources.append(name)

    total_parsed = len(all_jobs)
    logger.info("Total fetched: %d", total_parsed)

    # 2. Дедупликация + pre-filter (батчевые запросы к БД)
    # Версия pre-filter: отказы под старым отпечатком трактуются как unseen и
    # переоцениваются автоматически при смене критериев (PREFILTER_AUDIT.md §5.3).
    pf_version = _prefilter_version()
    seen_ids = storage.is_seen_batch([j.id for j in all_jobs], prefilter_version=pf_version)
    unseen = [j for j in all_jobs if j.id not in seen_ids]
    new_jobs = []
    near_miss = []
    for j in unseen:
        best = score_job(j)["best"]
        if best["passed_gate"] and best["recommend"]:
            j.match_role = best["role"]
            j.match_reasons = best["reasons"]
            new_jobs.append(j)
        elif best["passed_gate"] and best["score"] >= 40:
            near_miss.append((best["score"], j, best["reasons"]))

    # Провизорный seen — только детерминированно отсеянное, с отпечатком версии:
    # смена критериев переоткроет эти отказы. Кандидатов в AI помечает match_jobs
    # финальным вердиктом после успешного скоринга батча (сбой AI не теряет вакансии).
    ai_ids = {j.id for j in new_jobs}
    storage.mark_prefilter_seen([j for j in unseen if j.id not in ai_ids], pf_version)

    logger.info("After dedup: %d unseen | After pre-filter: %d to AI", len(unseen), len(new_jobs))
    # Пограничные вакансии — чтобы пересев был виден в логе без ручной диагностики
    for s, j, rs in sorted(near_miss, key=lambda x: -x[0])[:3]:
        logger.info("Near-miss [%d] %s @ %s | %s", s, j.title[:60], j.company[:30], "; ".join(rs)[:150])

    if not new_jobs:
        logger.info("No new relevant jobs found.")
        _send_zero_alert(total_parsed, len(unseen), 0, 0, threshold)
        return

    # 3. AI матчинг
    matched = match_jobs(new_jobs, threshold=threshold, batch_size=batch_size)
    logger.info("Matched %d jobs above threshold %d%%", len(matched), threshold)

    if not matched:
        _send_zero_alert(total_parsed, len(unseen), len(new_jobs), 0, threshold)
        return

    # 4. Отправка в Telegram
    sent = send_jobs_batch(matched)
    logger.info("Sent %d notifications", sent)

    # Дневной итог (если отправлено что-то)
    if sent > 0:
        send_daily_summary(total_parsed, sent, active_sources)


def _wait_for_network(timeout: int = 180) -> None:
    """Ждёт доступности сети/прокси перед стартом (нужно при автозапуске с ПК)."""
    proxy_host, proxy_port = "127.0.0.1", 10808

    for attempt in range(timeout // 10):
        # Сначала пробуем локальный прокси (Clash/V2Ray)
        try:
            with socket.create_connection((proxy_host, proxy_port), timeout=2):
                logger.info("Network proxy ready at %s:%d", proxy_host, proxy_port)
                return
        except OSError:
            pass

        # Если прокси нет — проверяем прямой интернет
        try:
            with socket.create_connection(("8.8.8.8", 53), timeout=2):
                logger.info("Direct internet access available (no proxy)")
                return
        except OSError:
            pass

        logger.info("Waiting for network... attempt %d/%d", attempt + 1, timeout // 10)
        time.sleep(10)

    logger.warning("Network not confirmed after %ds, proceeding anyway", timeout)


def main() -> None:
    cfg = _load_config()
    sched_cfg = cfg.get("scheduler", {})

    logger.info("Job Hunter starting. Waiting for network...")
    _wait_for_network(timeout=180)

    threading.Thread(target=run_listener, daemon=True).start()

    scheduler = BlockingScheduler(timezone="UTC")

    cron_expr = sched_cfg.get("cron")
    if cron_expr:
        # "0 6,14 * * *" → minute=0, hour=6,14, ...
        parts = cron_expr.split()
        scheduler.add_job(run_once, "cron",
                          minute=parts[0], hour=parts[1],
                          day=parts[2], month=parts[3], day_of_week=parts[4])
        logger.info("Job Hunter running. Schedule (UTC): %s", cron_expr)
        send_text(f"🤖 <b>Job Hunter запущен</b>\nРасписание: {cron_expr} UTC")
    else:
        interval = sched_cfg.get("interval_minutes", 60)
        scheduler.add_job(run_once, "interval", minutes=interval)
        logger.info("Job Hunter running. Interval: %d min", interval)
        send_text(f"🤖 <b>Job Hunter запущен</b>\nИнтервал: каждые {interval} мин.")

    # Первый запуск сразу
    run_once()

    scheduler.start()


if __name__ == "__main__":
    main()
