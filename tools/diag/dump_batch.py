"""Read-only дамп свежей пачки вакансий в JSONL на stdout.

Запуск в боевом контейнере (дамп с боевого IP):
    ssh vps-senko "docker exec job-hunter-job-hunter-1 python /app/tools/diag/dump_batch.py" > batch.jsonl

НЕ импортирует src.scheduler намеренно: его module-level logging.basicConfig
дописал бы вывод парсеров в боевой data/logs/job-hunter.log.
Список парсеров держать в синхроне с scheduler._build_parsers.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402


def build_parsers():
    """Тот же состав, что у боевого планировщика (src/parsers/registry.py).
    Раньше список дублировался здесь и разъезжался со scheduler — Habr выпадал
    из всех замеров воронки (HEALTH_AUDIT F11)."""
    from src.parsers.registry import build_parsers as _build

    cfg = yaml.safe_load((_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    return _build(cfg)


def fetch_jobs() -> list[dict]:
    """Параллельный fetch всех источников. Ошибки парсера не роняют дамп."""
    def run(p):
        try:
            return p.parse()
        except Exception:
            return []

    jobs = []
    parsers = build_parsers()
    with ThreadPoolExecutor(max_workers=len(parsers)) as pool:
        for f in as_completed({pool.submit(run, p) for p in parsers}):
            jobs.extend(f.result())
    return [
        {"id": j.id, "title": j.title, "company": j.company,
         "description": j.description, "source": j.source, "url": j.url}
        for j in jobs
    ]


if __name__ == "__main__":
    for row in fetch_jobs():
        print(json.dumps(row, ensure_ascii=False))
