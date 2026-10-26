import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .models import Job, MatchResult

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

            -- Трекер откликов: пайплайн заканчивался на «прислал карточку», из-за
            -- чего вторая половина поиска работы (на что откликнулся, кто молчит)
            -- жила вне системы. Заполняется кнопкой «✅ Откликнулся» в карточке.
            CREATE TABLE IF NOT EXISTS applications (
                job_id TEXT PRIMARY KEY,
                title TEXT,
                company TEXT,
                url TEXT,
                score INTEGER,
                applied_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'applied'
            );

            -- Пул непросмотренных вакансий HH (25.09.2026). Окно RSS — 20 свежих
            -- на запрос, поэтому всё, что бот не разобрал сразу (вышло между
            -- прогонами или отложено потолком пересмотра), к следующему прогону
            -- выпадало из выдачи и терялось. Здесь такие вакансии ждут вердикта.
            CREATE TABLE IF NOT EXISTS hh_pool (
                id TEXT PRIMARY KEY,
                job_json TEXT NOT NULL,
                added_at TEXT NOT NULL
            );

            -- Пул карточек LinkedIn (14.10.2026). Поиск отдаёт ~1700 карточек за
            -- прогон, описание — отдельный запрос, и за 45 минут стадии успевали
            -- 15–19 описаний: остальные карточки выбрасывались. Здесь карточка
            -- ждёт описания (его докачивают прогон и почасовой сбор), а с
            -- описанием — вердикта. id — без префикса li_.
            CREATE TABLE IF NOT EXISTS li_pool (
                id TEXT PRIMARY KEY,
                card_json TEXT NOT NULL,
                tier TEXT NOT NULL,
                description TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                added_at TEXT NOT NULL
            );

            -- Когда страна LinkedIn искалась последней: прогон берёт самые давние.
            CREATE TABLE IF NOT EXISTS li_geo_state (
                geo TEXT PRIMARY KEY,
                searched_at TEXT NOT NULL
            );

            -- Ключи near-дубликатов ОТПРАВЛЕННЫХ карточек (29.09.2026). Дедуп по
            -- ключу работал только внутри прогона: копия из другого города hh или
            -- репост в другом TG-канале в следующем прогоне приходила второй
            -- карточкой (аудит 28.09: «Тетрика» ×14, «межбиржевая торговля» ×7).
            -- Ключ — pre_filter.job_dedupe_key, окно — scheduler.dedupe_window_days.
            CREATE TABLE IF NOT EXISTS dedupe_keys (
                key TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            -- Сторож чатов hh (26.10.2026): о каких позициях уже прислана карточка.
            -- Ключ — msg:<id сообщения>, robot:<чат>:<хэш вопроса>, discard:<чат>.
            -- Робот повторяет вопрос раз в сутки новым сообщением, поэтому его
            -- ключ — текст вопроса, а не id: иначе карточка приходила бы каждый день.
            CREATE TABLE IF NOT EXISTS hh_chat_notified (
                key TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                notified_at TEXT NOT NULL
            );

            -- Мелкое состояние между запусками (сессия hh жива/истекла, счётчик
            -- сбоев сети): алерт шлётся на переходе, а не каждый запуск.
            CREATE TABLE IF NOT EXISTS kv_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
        # Миграция: id вакансии, копией которой признана эта (NULL — не копия).
        # Чтобы при разборе «почему не пришла» было видно, чей это дубль.
        if "dup_of" not in seen_cols:
            conn.execute("ALTER TABLE seen_jobs ADD COLUMN dup_of TEXT")


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


def mark_duplicate_seen(pairs: list) -> None:
    """Копия near-дубликата: финальный вердикт (prefilter_version NULL) с
    пометкой, чья это копия. Вход — [(Job, id представителя)].

    Финальный, а не провизорный: раньше копия, отброшенная дедупом, получала
    отказ под отпечатком предфильтра и воскресала при смене критериев — боевой
    случай 22→25.09.2026, «Аккаунт-менеджер» второй карточкой."""
    if not pairs:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO seen_jobs (id, source, title, url, seen_at, prefilter_version, dup_of) "
            "VALUES (?,?,?,?,?,NULL,?) "
            "ON CONFLICT(id) DO UPDATE SET prefilter_version=NULL, "
            "dup_of=excluded.dup_of, seen_at=excluded.seen_at",
            [(j.id, j.source, j.title, j.url, now, orig) for j, orig in pairs],
        )


def remember_dedupe_keys(pairs: list) -> None:
    """Запоминает ключи отправленных карточек: [(key, job_id)]. Повторная
    отправка того же ключа (после окна) обновляет дату и id."""
    if not pairs:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO dedupe_keys (key, job_id, created_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET job_id=excluded.job_id, "
            "created_at=excluded.created_at",
            [(k, jid, now) for k, jid in pairs],
        )


def known_dedupe_keys(keys: list, max_age_days: int = 30) -> dict:
    """{key: job_id} для ключей, отправленных не раньше max_age_days назад."""
    keys = list(set(keys))
    if not keys:
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    out = {}
    with get_conn() as conn:
        # Пачками: у SQLite лимит числа параметров в запросе.
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT key, job_id FROM dedupe_keys WHERE key IN ({placeholders}) "
                "AND created_at >= ?", [*chunk, cutoff],
            ).fetchall()
            out.update({r["key"]: r["job_id"] for r in rows})
    return out


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


# ── Пул непросмотренных вакансий HH ───────────────────────────────────────────

def _job_to_json(job: Job) -> str:
    d = asdict(job)
    d["published_at"] = job.published_at.isoformat() if job.published_at else None
    return json.dumps(d, ensure_ascii=False)


def _job_from_json(raw: str) -> Job:
    d = json.loads(raw)
    if d.get("published_at"):
        d["published_at"] = datetime.fromisoformat(d["published_at"])
    return Job(**d)


def pool_add(jobs: list) -> int:
    """Кладёт вакансии в пул; уже лежащие не перезаписывает. Возвращает число новых."""
    if not jobs:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO hh_pool (id, job_json, added_at) VALUES (?,?,?)",
            [(j.id, _job_to_json(j), now) for j in jobs],
        )
        return conn.total_changes - before


def pool_load(max_age_days: int = 14) -> tuple[list, int]:
    """Вакансии пула + число выброшенных по сроку.

    Срок нужен, чтобы пул не рос бесконечно, если приток превышает потолок
    пересмотра. Выброшенное считается и попадает в лог: потеря видна цифрой,
    а не происходит молча, как было до пула."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    with get_conn() as conn:
        expired = conn.execute("DELETE FROM hh_pool WHERE added_at < ?", (cutoff,)).rowcount
        rows = conn.execute("SELECT job_json FROM hh_pool").fetchall()
    return [_job_from_json(r["job_json"]) for r in rows], expired


def pool_prune_seen(prefilter_version: Optional[str] = None) -> int:
    """Убирает из пула всё, что уже получило вердикт или отказ под текущим
    отпечатком (та же логика, что в is_seen_batch). Возвращает число удалённых."""
    with get_conn() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM hh_pool").fetchall()]
    seen = is_seen_batch(ids, prefilter_version=prefilter_version)
    if not seen:
        return 0
    with get_conn() as conn:
        conn.executemany("DELETE FROM hh_pool WHERE id = ?", [(i,) for i in seen])
    return len(seen)


def pool_size() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) c FROM hh_pool").fetchone()["c"]


# ── Пул карточек LinkedIn ─────────────────────────────────────────────────────

def li_pool_add(cards: list[dict], tiers: dict[str, str]) -> int:
    """Кладёт карточки в пул (tiers: id -> high/low); лежащие не трогает."""
    if not cards:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO li_pool (id, card_json, tier, added_at) VALUES (?,?,?,?)",
            [(c["id"], json.dumps(c, ensure_ascii=False), tiers[c["id"]], now)
             for c in cards],
        )
        return conn.total_changes - before


def li_pool_pending(max_age_days: int = 7) -> tuple[list[dict], int]:
    """Карточки без описания (с полем tier) + число выброшенных по сроку.

    Срок — как окно поиска LinkedIn (неделя): старше вакансия обычно закрыта."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    with get_conn() as conn:
        expired = conn.execute("DELETE FROM li_pool WHERE added_at < ?", (cutoff,)).rowcount
        rows = conn.execute(
            "SELECT card_json, tier FROM li_pool WHERE description IS NULL "
            "ORDER BY added_at").fetchall()
    return [{**json.loads(r["card_json"]), "tier": r["tier"]} for r in rows], expired


def li_pool_set_description(card_id: str, description: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE li_pool SET description = ? WHERE id = ?",
                     (description, card_id))


def li_pool_fail(card_id: str, max_attempts: int = 3) -> None:
    """Описание не скачалось. После max_attempts карточка уходит из пула:
    пустой ответ бывает и у закрытой вакансии, и у серии 429 — различить
    их по телу нельзя, поэтому даём несколько попыток в разные часы."""
    with get_conn() as conn:
        conn.execute("UPDATE li_pool SET attempts = attempts + 1 WHERE id = ?", (card_id,))
        conn.execute("DELETE FROM li_pool WHERE id = ? AND attempts >= ?",
                     (card_id, max_attempts))


def li_pool_ready() -> list[tuple[dict, str]]:
    """Карточки с описанием, ждущие вердикта: [(card, description)]."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT card_json, description FROM li_pool WHERE description IS NOT NULL"
        ).fetchall()
    return [(json.loads(r["card_json"]), r["description"]) for r in rows]


def li_pool_prune_seen(prefilter_version: Optional[str] = None) -> int:
    """Убирает из пула всё, что получило вердикт под текущим отпечатком."""
    with get_conn() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM li_pool").fetchall()]
    seen = is_seen_batch([f"li_{i}" for i in ids], prefilter_version=prefilter_version)
    if not seen:
        return 0
    with get_conn() as conn:
        conn.executemany("DELETE FROM li_pool WHERE id = ?", [(s[3:],) for s in seen])
    return len(seen)


def li_pool_stats() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT tier, description IS NOT NULL ready, COUNT(*) c FROM li_pool "
            "GROUP BY tier, ready").fetchall()
    out = {"high": 0, "low": 0, "ready": 0}
    for r in rows:
        if r["ready"]:
            out["ready"] += r["c"]
        else:
            out[r["tier"]] = out.get(r["tier"], 0) + r["c"]
    return out


def li_geo_oldest(names: list[str], n: int) -> list[str]:
    """n стран, которые искались давнее всех (ни разу — первыми, по порядку конфига)."""
    with get_conn() as conn:
        last = {r["geo"]: r["searched_at"] for r in
                conn.execute("SELECT geo, searched_at FROM li_geo_state").fetchall()}
    order = sorted(range(len(names)), key=lambda i: (last.get(names[i], ""), i))
    return [names[i] for i in order[:n]]


def li_geo_mark(name: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO li_geo_state (geo, searched_at) VALUES (?,?)",
                     (name, now))


# ── Трекер откликов ───────────────────────────────────────────────────────────

def save_application(job_id: str, title: str = "", company: str = "",
                     url: str = "", score: Optional[int] = None) -> bool:
    """Отмечает отклик. Возвращает False, если отклик уже был (повторное нажатие)."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        if existing:
            return False
        conn.execute(
            """INSERT INTO applications
               (job_id, title, company, url, score, applied_at, status)
               VALUES (?,?,?,?,?,?,'applied')""",
            (job_id, title, company, url, score,
             datetime.now(timezone.utc).isoformat()),
        )
    return True


def list_applications(limit: int = 20) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT job_id, title, company, url, score, applied_at, status
               FROM applications ORDER BY applied_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def application_stats(stale_days: int = 14) -> dict:
    """Сводка: всего, за 7 дней, без ответа дольше stale_days."""
    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()
    stale_before = (now - timedelta(days=stale_days)).isoformat()
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM applications").fetchone()["c"]
        last7 = conn.execute(
            "SELECT COUNT(*) c FROM applications WHERE applied_at >= ?", (week_ago,)
        ).fetchone()["c"]
        stale = conn.execute(
            """SELECT COUNT(*) c FROM applications
               WHERE status = 'applied' AND applied_at < ?""", (stale_before,)
        ).fetchone()["c"]
    return {"total": total, "last7": last7, "stale": stale, "stale_days": stale_days}


# ── Сторож чатов hh ───────────────────────────────────────────────────────────

def hh_chat_known(keys: list) -> set:
    """Ключи позиций, о которых карточка уже уходила."""
    if not keys:
        return set()
    known: set = set()
    with get_conn() as conn:
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT key FROM hh_chat_notified WHERE key IN ({marks})", chunk
            ).fetchall()
            known.update(r["key"] for r in rows)
    return known


def hh_chat_mark(pairs: list) -> None:
    """Запоминает позиции как уведомлённые. pairs — [(ключ, id чата)]."""
    if not pairs:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO hh_chat_notified (key, chat_id, notified_at) "
            "VALUES (?,?,?)",
            [(k, str(c), now) for k, c in pairs],
        )


def state_get(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM kv_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def state_set(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO kv_state (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, value, datetime.now(timezone.utc).isoformat()),
        )
