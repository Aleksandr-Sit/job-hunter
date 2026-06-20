import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from .models import Job, MatchResult


DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
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

            CREATE TABLE IF NOT EXISTS saved_jobs (
                job_id TEXT PRIMARY KEY,
                title TEXT,
                company TEXT,
                url TEXT,
                score INTEGER,
                saved_at TEXT NOT NULL
            );
        """)


def is_seen_batch(job_ids: list) -> set:
    if not job_ids:
        return set()
    placeholders = ",".join("?" * len(job_ids))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT id FROM seen_jobs WHERE id IN ({placeholders})", job_ids
        ).fetchall()
    return {row["id"] for row in rows}


def mark_seen(job: Job) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_jobs (id, source, title, url, seen_at) VALUES (?,?,?,?,?)",
            (job.id, job.source, job.title, job.url, datetime.now(timezone.utc).isoformat()),
        )


def mark_seen_batch(jobs: list) -> None:
    if not jobs:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO seen_jobs (id, source, title, url, seen_at) VALUES (?,?,?,?,?)",
            [(j.id, j.source, j.title, j.url, now) for j in jobs],
        )


def get_cached_match(job_id: str) -> Optional[MatchResult]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM match_cache WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        return MatchResult(
            job_id=row["job_id"],
            score=row["score"],
            why_fits=json.loads(row["why_fits"]),
            watch_out=json.loads(row["watch_out"]),
            recommendation=row["recommendation"],
        )


def save_match(result: MatchResult) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO match_cache
               (job_id, score, why_fits, watch_out, recommendation, matched_at)
               VALUES (?,?,?,?,?,?)""",
            (
                result.job_id,
                result.score,
                json.dumps(result.why_fits, ensure_ascii=False),
                json.dumps(result.watch_out, ensure_ascii=False),
                result.recommendation,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def save_job(job: Job, score: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO saved_jobs
               (job_id, title, company, url, score, saved_at) VALUES (?,?,?,?,?,?)""",
            (job.id, job.title, job.company, job.url, score, datetime.now(timezone.utc).isoformat()),
        )
