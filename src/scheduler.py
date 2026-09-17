"""
Основной цикл: парсинг → фильтрация → матчинг → уведомление.
Запускается по расписанию через APScheduler.
"""
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

from . import storage
from .bot.callback_handler import run_listener
from .bot.notifier import send_daily_summary, send_jobs_batch, send_text
from .log_redact import RedactingFilter
from .matcher.cerebras_matcher import match_jobs
from .matcher.pre_filter import _prefilter_version, dedupe_jobs, is_soft_reject, score_job
from .models import Job

load_dotenv(Path(__file__).parent.parent / ".env")

_LOG_FILE = Path(__file__).parent.parent / "data" / "logs" / "job-hunter.log"
_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

_handlers = [
    logging.StreamHandler(),
    # Без ротации лог рос неограниченно: 64 МБ за два месяца на диске 9.7 ГБ.
    RotatingFileHandler(_LOG_FILE, maxBytes=5_000_000, backupCount=3,
                        encoding="utf-8"),
]
# Фильтр вешаем на ХЕНДЛЕРЫ, а не на отдельные логгеры: так он ловит запись
# независимо от того, какая библиотека её породила (см. src/log_redact.py).
for _h in _handlers:
    _h.addFilter(RedactingFilter())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=_handlers,
)

# Второй рубеж — глушим болтливые HTTP-логгеры. Один только этот список защитой не
# считается: он уже оказывался неполным (после httpx нашёлся httpx2 с тем же
# поведением), поэтому основную работу делает фильтр выше.
for _noisy in ("httpx", "httpx2", "httpcore", "telegram", "apscheduler", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

_CONFIG = Path(__file__).parent.parent / "config" / "settings.yaml"


def _load_config() -> dict:
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))


def _build_parsers(cfg: dict) -> list:
    """Состав источников — в src/parsers/registry.py (один список на проект,
    его же использует tools/diag/dump_batch.py)."""
    from .parsers.registry import build_parsers
    return build_parsers(cfg)


def _send_zero_alert(total: int, unseen: int, prefiltered: int, matched: int, threshold: int) -> None:
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = [f"🔍 <b>Прогон {ts} — вакансий не отправлено</b>"]
    lines.append(f"Собрано: {total} | Новых: {unseen} | Pre-filter: {prefiltered} | AI ≥{threshold}%: {matched}")
    from .matcher.cerebras_matcher import last_run_stats
    failed = last_run_stats.get("failed_batches", 0)
    if unseen == 0:
        lines.append("Причина: все уже видели раньше (дедуп)")
    elif prefiltered == 0:
        lines.append("Причина: ни одна не прошла pre-filter (роль/домен/стоп-слова)")
    elif failed and failed >= last_run_stats.get("batches", 0):
        # AI не ответил вообще — «оценил ниже порога» было бы ложной причиной
        lines.append(
            f"⚠️ Причина: <b>AI недоступен</b> — упали все {failed} батчей, "
            f"{last_run_stats.get('unscored', 0)} вакансий не оценены."
        )
        lines.append("Отказали все провайдеры: проверь квоты и ключи. "
                     "Вакансии не потеряны — пересчитаются.")
    elif failed:
        lines.append(
            f"Причина: AI оценил ниже порога. ⚠️ Но {failed} из "
            f"{last_run_stats.get('batches', 0)} батчей упали "
            f"({last_run_stats.get('unscored', 0)} вакансий не оценены)."
        )
    else:
        lines.append("Причина: AI оценил ниже порога")
    send_text("\n".join(lines))


def _enrich_hh_and_rescore(jobs: list, limit: int = 120) -> list:
    """Дотягивает полные описания вакансий с HH и пересчитывает по ним отбор.

    RSS отдаёт ~125 символов, поэтому до этой стадии вакансии с HH оценивались
    фактически по заголовку: все получали похожий балл и не различались между
    собой, а ночные смены, офис и требования к английскому были не видны вообще.

    Замер 19.08.2026 на 56 вакансиях: 7 отсеялись (две — ночные смены как режим),
    у остальных баллы разъехались (AML-специалист 50 → 90, комплаенс 50 → 32).
    """
    from .parsers.hh_enrich import enrich

    hh_before = sum(1 for j in jobs if (j.source or "") == "hh.ru")
    if not hh_before:
        return jobs

    stats = enrich(jobs, limit=limit)
    if not stats.enriched:
        return jobs   # ничего не дотянулось — отбор пересчитывать не на чем

    kept, dropped = [], []
    for j in jobs:
        if (j.source or "") != "hh.ru":
            kept.append(j)
            continue
        best = score_job(j)["best"]
        if best["passed_gate"] and best["recommend"]:
            j.match_role = best["role"]
            j.match_reasons = best["reasons"]
            kept.append(j)
        else:
            dropped.append((j, best))

    if dropped:
        logger.info("HH после дообогащения отсеяно: %d из %d", len(dropped), hh_before)
        for j, best in dropped[:5]:
            why = "; ".join(best.get("reasons") or []) or "балл ниже порога"
            logger.info("  отсеяна [%d] %s | %s", best["score"], j.title[:60], why[:110])

    if not stats.healthy:
        send_text(
            "⚠️ <b>HH: дообогащение почти не работает</b>\n"
            f"Извлечено {stats.enriched} из {stats.attempted} описаний.\n"
            "Либо антибот hh.ru на объёме (лечится паузой _PAUSE), либо "
            "сменилась вёрстка (тогда — селекторы). Пока вакансии с HH "
            "снова оцениваются по заголовку."
        )
    return kept


def _rescue_order(jobs: list) -> list:
    """Порядок очереди пересмотра: слежка за работодателями → свежие → старые.

    Выдача HH идёт в порядке запросов, слежка — в самом конце, поэтому потолок
    пересмотра срезал именно её (ревизия 17.09.2026). Свежесть вторым ключом:
    не влезшие в потолок остаются в очереди (см. `_rescue_hh_rejects`), и
    порядок по дате стабилен — старое ждёт свободного места, а не вытесняет
    свежее по кругу. Без даты — в конец.
    """
    def key(j):
        ts = j.published_at.timestamp() if j.published_at else float("-inf")
        return (not j.raw.get("employer_watch"), -ts)
    return sorted(jobs, key=key)


def _rescue_hh_rejects(jobs: list, limit: int = 220) -> tuple[list, list]:
    """Пересматривает отказы HH, вынесенные по обрезанному описанию.

    Зачем. RSS отдаёт медианно 126 символов, то есть решение «нет ролевых слов /
    нет крипто-контекста» принимается фактически по заголовку. Дообогащение до этой
    правки применялось ТОЛЬКО к выжившим (`_enrich_hh_and_rescore`), поэтому могло
    отбор лишь ужесточить — вернуть ошибочно отсеянное было нечем.

    Замер на боевом прогоне 05.09.2026: из 227 новых вакансий HH предфильтр пропускал
    4, ещё 17 упирались в настоящие блокеры (английский, опыт, dev-роль), а 206
    отсеивались только по роли/домену. Дообогащение случайной выборки из этих 206
    вернуло в отбор 19%.

    Берём только «мягкие» отказы (см. `is_soft_reject`): отказ по английскому или
    гражданству полным описанием не переворачивается, качать его — впустую тратить
    ~0.8 МБ и 0.87 с на вакансию.

    Возвращает `(recovered, deferred)`. `deferred` — не пересмотренные: хвост за
    потолком и, если hh.ru не отдал описания (антибот), цели без описания. Их НЕ
    помечают увиденными — иначе они не вернулись бы до следующей смены критериев.
    Потолок упирался в каждом прогоне (лог 14–17.09.2026: «219 из 220»).
    """
    if not jobs:
        return [], []
    from .parsers.hh_enrich import enrich

    queue = _rescue_order(jobs)
    targets, deferred = queue[:limit], queue[limit:]
    before = {j.id: j.description for j in targets}
    stats = enrich(targets, limit=limit)
    watched = sum(1 for j in jobs if j.raw.get("employer_watch"))

    if not stats.healthy:
        # Единичный сбой страницы при здоровом прогоне помечается как обычно: иначе
        # битая страница вечно занимала бы голову очереди.
        unfetched = [j for j in targets if j.description == before[j.id]]
        deferred = unfetched + deferred
        targets = [j for j in targets if j.description != before[j.id]]

    recovered = []
    for j in targets:
        best = score_job(j)["best"]
        if best["passed_gate"] and best["recommend"]:
            j.match_role = best["role"]
            j.match_reasons = best["reasons"]
            recovered.append(j)

    logger.info("HH пересмотр отказов: в очереди %d (слежка %d), пересмотрено %d "
                "(дообогащено %d), отложено %d, вернулось в отбор %d",
                len(jobs), watched, len(targets), stats.enriched, len(deferred),
                len(recovered))
    for j in recovered[:5]:
        logger.info("  возвращена [%s] %s", j.match_role, j.title[:65])
    return recovered, deferred


def _warn_if_provider_switched() -> None:
    """Сообщает, что основной AI-провайдер отказал, а работу вытянул запасной.

    Без этого переключение проходит незаметно: вакансии приходят как обычно,
    и о том, что у основного кончилась квота, станет известно только когда
    откажет и запасной.
    """
    from .matcher.cerebras_matcher import last_run_stats
    if not last_run_stats.get("switched"):
        return
    used = last_run_stats.get("provider") or "запасной"
    send_text(
        "⚠️ <b>Основной AI-провайдер отказал</b>\n"
        f"Вакансии оценил запасной — <code>{used}</code>.\n"
        "Проверь квоту и ключ основного, пока запас не кончился тоже."
    )


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
    hh_retry = []
    for j in unseen:
        scored = score_job(j)
        best = scored["best"]
        if best["passed_gate"] and best["recommend"]:
            j.match_role = best["role"]
            j.match_reasons = best["reasons"]
            new_jobs.append(j)
            continue
        if best["passed_gate"] and best["score"] >= 40:
            near_miss.append((best["score"], j, best["reasons"]))
        # Отказ HH, вынесенный по 126-символьному огрызку, — кандидат на пересмотр
        # с полным описанием (см. _rescue_hh_rejects).
        if (j.source or "") == "hh.ru" and is_soft_reject(scored):
            hh_retry.append(j)

    # Near-дубликаты схлопываем ДО обращения к AI. Раньше это делалось только перед
    # отправкой (шаг 4), поэтому до Telegram копии не доходили — но каждая успевала
    # съесть свою долю бюджета Cerebras. Замер свежей пачки 15.08.2026: Social
    # Discovery Group ×3, Coinbase «Senior IT Automation Engineer» ×2, Kraken «Growth
    # Workflow Manager» ×4 (две пары различались только двойным пробелом в заголовке).
    # Место выбрано до `ai_ids`: тогда отброшенные копии попадут в mark_prefilter_seen
    # ниже и не вернутся на следующем прогоне.
    before_ai = len(new_jobs)
    new_jobs = dedupe_jobs(new_jobs)
    if before_ai != len(new_jobs):
        logger.info("Near-дубликаты схлопнуты до AI: %d → %d", before_ai, len(new_jobs))

    # 2б. Дообогащение HH и ПОВТОРНЫЙ отбор.
    # Порядок важен вдвойне. После дедупа — чтобы не качать копии. До `ai_ids` —
    # чтобы отсеянные здесь попали в mark_prefilter_seen ниже и не вернулись
    # на следующем прогоне. Без повторного score_job вся стадия бессмысленна:
    # описание приехало бы, а решение осталось бы принятым по заголовку.
    deferred_ids: set = set()
    if cfg.get("hh_enrich", {}).get("enabled", True):
        _hh_cfg = cfg.get("hh_enrich", {})
        new_jobs = _enrich_hh_and_rescore(new_jobs, limit=_hh_cfg.get("limit", 120))
        # ...и только потом пересматриваем отказы: дедуп уже прошёл, копии не качаем.
        rescued, deferred = _rescue_hh_rejects(hh_retry, limit=_hh_cfg.get("retry_limit", 220))
        deferred_ids = {j.id for j in deferred}
        if rescued:
            new_jobs = dedupe_jobs(new_jobs + rescued)

    # Провизорный seen — только детерминированно отсеянное, с отпечатком версии:
    # смена критериев переоткроет эти отказы. Кандидатов в AI помечает match_jobs
    # финальным вердиктом после успешного скоринга батча (сбой AI не теряет вакансии).
    # Отложенные пересмотром не помечаются: остаются в очереди следующего прогона.
    ai_ids = {j.id for j in new_jobs}
    storage.mark_prefilter_seen(
        [j for j in unseen if j.id not in ai_ids and j.id not in deferred_ids], pf_version)

    logger.info("After dedup: %d unseen (из них отложено до пересмотра HH: %d) | "
                "After pre-filter: %d to AI", len(unseen), len(deferred_ids), len(new_jobs))
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
    _warn_if_provider_switched()

    if not matched:
        _send_zero_alert(total_parsed, len(unseen), len(new_jobs), 0, threshold)
        return

    # 4. Отправка в Telegram (сначала схлопываем near-дубликаты: одна роль,
    # пришедшая с разных бордов/локаций, имеет разные id и проходит дедуп по id)
    before = len(matched)
    matched = dedupe_jobs(matched)
    if before != len(matched):
        logger.info("Near-дубликаты схлопнуты: %d → %d", before, len(matched))

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

    # Первый запуск сразу. Под защитой: без неё падение стартового прогона роняло
    # контейнер целиком, а `restart: unless-stopped` поднимал его заново — карусель
    # перезапусков вместо работы по расписанию (ревизия 17.09.2026).
    try:
        run_once()
    except Exception:
        logger.exception("Стартовый прогон упал")
        try:
            send_text("⚠️ <b>Стартовый прогон упал</b>\n"
                      "Бот жив и работает по расписанию, но первый прогон "
                      "не доделан. Причина — в логе (traceback).")
        except Exception:
            logger.exception("Не удалось отправить алерт о падении стартового прогона")

    scheduler.start()


if __name__ == "__main__":
    main()
