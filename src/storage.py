import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from .models import MatchResult


DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS seen_jobs (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT,
                url TEXT,
                seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS match_cache (
                job_id TEXT PRIMARY KEY,
                score INTEGER NOT NULL,
                why_fits TEXT NOT NULL,
                watch_out TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                matched_at TEXT NOT NULL
            );
        """)
        # Миграция: версия скоринга (промпт+профиль+критерии). Кэш с другой
        # версией считается устаревшим и пересчитывается — иначе смена критериев
        # оставляла бы старые баллы навсегда (PIPELINE_REVIEW.md M1).
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(match_cache)")}
        if "scoring_version" not in cols:
            conn.execute("ALTER TABLE match_cache ADD COLUMN scoring_version TEXT")

        # Миграция: версия pre-filter для seen_jobs. NULL = финальный вердикт
        # (AI оценил / отправлено), seen навсегда. Непустое значение = провизорный
        # отказ pre-filter под этим отпечатком: устаревает при смене критериев и
        # снова попадает в воронку (PREFILTER_AUDIT.md §5.3). Существующие строки
        # получают NULL = вердикт — версионирование действует для новых отказов.
        seen_cols = {r["name"] for r in conn.execute("PRAGMA table_info(seen_jobs)")}
        if "prefilter_version" not in seen_cols:
            conn.execute("ALTER TABLE seen_jobs ADD COLUMN prefilter_version TEXT")


def is_seen_batch(job_ids: list, prefilter_version: Optional[str] = None) -> set:
    """Строки, которые нужно считать seen (исключить из воронки).

    prefilter_version=None — обратная совместимость: любая строка = seen.
    Иначе seen = финальный вердикт (prefilter_version IS NULL) ИЛИ провизорный
    отказ под ТЕКУЩИМ отпечатком. Отказы со старым отпечатком → не seen (смена
    критериев автоматически переоткрывает их, PREFILTER_AUDIT.md §5.3)."""
    if not job_ids:
        return set()
    placeholders = ",".join("?" * len(job_ids))
    with get_conn() as conn:
        if prefilter_version is None:
            rows = conn.execute(
                f"SELECT id FROM seen_jobs WHERE id IN ({placeholders})", job_ids
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT id FROM seen_jobs WHERE id IN ({placeholders}) "
                "AND (prefilter_version IS NULL OR prefilter_version = ?)",
                [*job_ids, prefilter_version],
            ).fetchall()
    return {row["id"] for row in rows}


def mark_seen_batch(jobs: list) -> None:
    """Финальный вердикт: prefilter_version = NULL, seen навсегда. Апгрейдит
    провизорный отказ до вердикта, если вакансия дошла до AI-скоринга."""
    if not jobs:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO seen_jobs (id, source, title, url, seen_at, prefilter_version) "
            "VALUES (?,?,?,?,?,NULL) "
            "ON CONFLICT(id) DO UPDATE SET prefilter_version=NULL, seen_at=excluded.seen_at",
            [(j.id, j.source, j.title, j.url, now) for j in jobs],
        )


def mark_prefilter_seen(jobs: list, version: str) -> None:
    """Провизорный отказ pre-filter под отпечатком version. Не понижает
    существующий вердикт (prefilter_version IS NULL остаётся NULL)."""
    if not jobs:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO seen_jobs (id, source, title, url, seen_at, prefilter_version) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET prefilter_version=excluded.prefilter_version, "
            "seen_at=excluded.seen_at WHERE seen_jobs.prefilter_version IS NOT NULL",
            [(j.id, j.source, j.title, j.url, now, version) for j in jobs],
        )


def get_cached_match(job_id: str, version: Optional[str] = None) -> Optional[MatchResult]:
    """Кэш-хит только если версия скоринга совпадает. version=None — без проверки
    (обратная совместимость). Иная версия трактуется как промах → пересчёт."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM match_cache WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        if version is not None and row["scoring_version"] != version:
            return None
        return MatchResult(
            job_id=row["job_id"],
            score=row["score"],
            why_fits=json.loads(row["why_fits"]),
            watch_out=json.loads(row["watch_out"]),
            recommendation=row["recommendation"],
        )


def save_match(result: MatchResult, version: Optional[str] = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO match_cache
               (job_id, score, why_fits, watch_out, recommendation, matched_at, scoring_version)
               VALUES (?,?,?,?,?,?,?)""",
            (
                result.job_id,
                result.score,
                json.dumps(result.why_fits, ensure_ascii=False),
                json.dumps(result.watch_out, ensure_ascii=False),
                result.recommendation,
                datetime.now(timezone.utc).isoformat(),
                version,
            ),
        )
