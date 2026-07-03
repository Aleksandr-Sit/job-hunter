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
    from src.parsers.hh_parser import HHParser
    from src.parsers.telegram_parser import TelegramParser
    from src.parsers.web.remoteok import RemoteOKParser
    from src.parsers.web.cryptojoblist import CryptoJobListParser
    from src.parsers.web.laborx import LaborXParser
    from src.parsers.web.remote3 import Remote3Parser
    from src.parsers.web.wellfound import WellFoundParser
    from src.parsers.web.contra import ContraParser
    from src.parsers.web.ashby import AshbyParser
    from src.parsers.web.greenhouse import GreenhouseParser
    from src.parsers.web.lever import LeverParser
    from src.parsers.web.linkedin import LinkedInParser

    cfg = yaml.safe_load((_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    p = cfg.get("parsers", {})
    out = []
    if p.get("hh", {}).get("enabled", True): out.append(HHParser())
    if p.get("remoteok", {}).get("enabled", True): out.append(RemoteOKParser())
    if p.get("cryptojoblist", {}).get("enabled", True): out.append(CryptoJobListParser())
    if p.get("laborx", {}).get("enabled", True): out.append(LaborXParser())
    if p.get("remote3", {}).get("enabled", True): out.append(Remote3Parser())
    if p.get("wellfound", {}).get("enabled", False): out.append(WellFoundParser())
    if p.get("contra", {}).get("enabled", False): out.append(ContraParser())
    if p.get("ashby", {}).get("enabled", True): out.append(AshbyParser())
    if p.get("greenhouse", {}).get("enabled", True): out.append(GreenhouseParser())
    if p.get("lever", {}).get("enabled", True): out.append(LeverParser())
    if p.get("linkedin", {}).get("enabled", False): out.append(LinkedInParser())
    if p.get("telegram", {}).get("enabled", True): out.append(TelegramParser())
    return out


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
