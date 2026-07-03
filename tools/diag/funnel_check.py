"""Воронка pre-filter на пачке вакансий: гейты → баллы → порог. Read-only.

Свежая пачка (боевой контейнер, ~2 мин на fetch):
    ssh vps-senko "docker exec job-hunter-job-hunter-1 python /app/tools/diag/funnel_check.py"

Готовый дамп (например, A/B нового кода в локальном образе):
    docker compose run --rm -T --no-deps job-hunter \
        python /app/tools/diag/funnel_check.py /app/data/diag_batch.jsonl
"""
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import median

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.matcher.pre_filter import score_vacancy, CRITERIA  # noqa: E402


def load_jobs() -> list[dict]:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    from dump_batch import fetch_jobs
    return fetch_jobs()


def main() -> None:
    jobs = load_jobs()

    # Группировка строго по job["source"] (НЕ по имени парсера — они различаются)
    per_source = Counter(j["source"] for j in jobs)
    desc_empty = Counter(j["source"] for j in jobs if not (j["description"] or "").strip())

    gate_fail = Counter()
    passers = []   # (score, role, job, reasons, recommend)
    for j in jobs:
        res = {r: score_vacancy(j["title"], j["description"], r) for r in CRITERIA["roles"]}
        gated = {r: v for r, v in res.items() if v["passed_gate"]}
        if not gated:
            gate_fail[" | ".join(sorted({v["reasons"][0] for v in res.values()}))] += 1
        else:
            best_role = max(gated, key=lambda r: gated[r]["score"])
            b = gated[best_role]
            passers.append((b["score"], best_role, j, b["reasons"], b["recommend"]))

    passers.sort(key=lambda x: -x[0])
    scores = [s for s, *_ in passers]
    buckets = Counter(f"{(s // 10) * 10:02d}-{(s // 10) * 10 + 9:02d}" for s in scores)
    thresholds = {r: CRITERIA["roles"][r]["threshold"] for r in CRITERIA["roles"]}

    print("=== FUNNEL ===")
    print(f"total={len(jobs)} gate_passed={len(passers)} "
          f"recommend={sum(1 for p in passers if p[4])} (пороги ролей: {thresholds})")
    if scores:
        print(f"score min/med/max: {min(scores)}/{median(scores)}/{max(scores)}")
        print(f"cutoffs: ge55={sum(1 for s in scores if s >= 55)} ge50={sum(1 for s in scores if s >= 50)} "
              f"ge45={sum(1 for s in scores if s >= 45)} ge40={sum(1 for s in scores if s >= 40)}")
        print(f"hist: {dict(sorted(buckets.items()))}")

    print("=== PER SOURCE (count / empty desc) ===")
    for src, n in per_source.most_common():
        print(f"  {src:22s} {n:5d} / {desc_empty.get(src, 0)}")

    print("=== GATE FAIL TOP ===")
    for reason, n in gate_fail.most_common(12):
        print(f"  {n:5d}  {reason[:140]}")

    print("=== RECOMMEND (пойдут в AI) ===")
    for s, role, j, reasons, rec in passers:
        if rec:
            print(f"[{s:3d}] {role:13s} {j['source']:14s} | {j['title'][:65]} @ {j['company'][:25]}")

    print("=== NEAR-MISS (40..порог) ===")
    for s, role, j, reasons, rec in passers:
        if not rec and s >= 40:
            print(f"[{s:3d}] {role:13s} {j['source']:14s} | {j['title'][:65]} @ {j['company'][:25]}")
            print(f"      {'; '.join(reasons)[:170]}")

    print("=== RAW SAMPLES (поля заполнены?) ===")
    for j in jobs[:3]:
        print(f"src={j['source']} title={j['title'][:60]!r} desc_len={len(j['description'] or '')}")


if __name__ == "__main__":
    main()
