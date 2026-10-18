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

    # LinkedIn с 15.10.2026 работает через базу: пул li_pool и ротация стран
    # li_geo_state. Дамп в боевом контейнере писал бы в БОЕВУЮ базу — забил бы пул
    # уже оценёнными карточками и сбил ротацию. Поэтому — отдельная временная БД.
    import tempfile

    from src import storage
    import src.matcher.title_triage as triage
    storage.DB_PATH = Path(tempfile.mkdtemp()) / "dump.db"
    storage.init_db()
    # Батч — снимок ВСЕГО потока: карточки, которые бот режет по заголовку (drop),
    # тоже нужны — иначе A/B по гейтам заголовка их не увидит.
    triage.title_triage = lambda title, company=None: "high"

    cfg = yaml.safe_load((_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    parsers = _build(cfg)
    # Батч для замеров — снимок ВСЕГО потока, а не только новых вакансий: боевой
    # LinkedIn пропускает описания увиденных (skip_seen), здесь они нужны.
    for p in parsers:
        p.skip_seen = False
        if p.name == "linkedin":
            # Все страны и время на описания всех карточек (~1700 × 3,5 с), но с
            # запасом до планового прогона: дамп и прогон делят IP и лимит 429.
            p.geos_per_run = 0
            p.max_minutes = 135
            p.max_descriptions = 5000
    return parsers


def fetch_jobs() -> list[dict]:
    """Параллельный fetch всех источников. Ошибки парсера не роняют дамп."""
    from src.parsers.normalize import clean_job_fields

    def run(p):
        try:
            jobs = p.parse()
            for j in jobs:   # как в scheduler.run_once — замер видит те же заголовки
                clean_job_fields(j)
            return jobs
        except Exception:
            return []

    jobs = []
    parsers = build_parsers()
    with ThreadPoolExecutor(max_workers=len(parsers)) as pool:
        for f in as_completed({pool.submit(run, p) for p in parsers}):
            jobs.extend(f.result())
    # location/is_remote обязательны: боевой score_job подмешивает локацию в текст,
    # и без неё замер терял штраф «только офис» (ревизия 17.09.2026).
    return [
        {"id": j.id, "title": j.title, "company": j.company,
         "description": j.description, "source": j.source, "url": j.url,
         "location": j.location, "is_remote": j.is_remote}
        for j in jobs
    ]


if __name__ == "__main__":
    for row in fetch_jobs():
        print(json.dumps(row, ensure_ascii=False))
