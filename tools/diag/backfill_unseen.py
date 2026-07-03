"""Возврат вакансий в очередь: удаляет id из seen_jobs → следующий крон-прогон
подхватит их штатным пайплайном (pre-filter → AI → Telegram).

По умолчанию — dry-run (только считает). Удаление — флагом --delete, после «ок».

    scp ids.txt vps-senko:/opt/job-hunter/data/backfill_ids.txt
    ssh vps-senko "docker exec job-hunter-job-hunter-1 python /app/tools/diag/backfill_unseen.py /app/data/backfill_ids.txt"
    ssh vps-senko "docker exec job-hunter-job-hunter-1 python /app/tools/diag/backfill_unseen.py /app/data/backfill_ids.txt --delete"

Файл id: по одному id на строку (форматы hh_*, gh_*, ab_*, lv_* и т.д.).
"""
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
DB = _ROOT / "data" / "jobs.db"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: backfill_unseen.py <ids-file> [--delete]")
    ids = [line.strip() for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if line.strip()]
    do_delete = "--delete" in sys.argv[2:]

    conn = sqlite3.connect(DB, timeout=10)
    ph = ",".join("?" * len(ids))
    seen = conn.execute(f"SELECT COUNT(*) FROM seen_jobs WHERE id IN ({ph})", ids).fetchone()[0]
    cached = conn.execute(f"SELECT COUNT(*) FROM match_cache WHERE job_id IN ({ph})", ids).fetchone()[0]
    print(f"ids={len(ids)} in_seen_jobs={seen} with_ai_cache={cached} "
          f"(кэшированные возьмутся из кэша, ниже AI-порога — не отправятся)")

    if not do_delete:
        print("dry-run: ничего не удалено. Для удаления добавь --delete")
        return

    before = conn.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()[0]
    conn.executemany("DELETE FROM seen_jobs WHERE id = ?", [(i,) for i in ids])
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()[0]
    print(f"seen_jobs: {before} -> {after} (удалено {before - after})")


if __name__ == "__main__":
    main()
