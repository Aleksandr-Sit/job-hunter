"""source_probe — проверка источника вакансий ЖИВЫМ fetch'ем с боевого IP.

Зачем: веб-поиск и описания врут. Эта сессия поймала 3 раза:
  - Bybit «нет ATS» (веб-аудит) → реально Greenhouse, 129/57 ops.
  - chainlink-labs (веб-поиск рекомендовал Ashby) → 404.
  - Habr specialization=crypto (ждали крипту) → криптография/ИБ, 0 web3.
Правило: НИКОГДА не добавляй источник, не прогнав его через этот скрипт с VPS.

Запуск (на боевом IP — с хоста VPS или через docker exec):
    ssh vps-senko "python3 /app/tools/diag/source_probe.py ats bybit bitget kucoin gate"
    ssh vps-senko "python3 /app/tools/diag/source_probe.py board https://cryptocurrencyjobs.co/operations/"

Режимы:
  ats   <slug> [...]   — тест slug на Greenhouse/Lever/Ashby: total + ops-like титулы.
  board <url>  [...]   — забираемость (HTTP/размер) + тип фида (RSS/NEXT/Algolia/GraphQL/HTML)
                         + число job-ссылок (признак серверного рендера vs SPA).
"""
import json
import re
import sys
import urllib.error
import urllib.request

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# «ops-подобный» титул = целевой сегмент кандидата (ops/support/compliance/...)
OPS = ("operation", "ops", "support", "community", "compliance", "treasury",
       "custody", "settlement", "kyc", "aml", "analyst", "specialist",
       "associate", "moderator", "success", "onboarding", "risk", "trust",
       "customer", "player", "staking")

_ATS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{}/jobs", "jobs", "title"),
    "lever":      ("https://api.lever.co/v0/postings/{}?mode=json", None, "text"),
    "ashby":      ("https://api.ashbyhq.com/posting-api/job-board/{}", "jobs", "title"),
}


def _get(url, raw=False, timeout=15):
    req = urllib.request.Request(url, headers=_UA)
    data = urllib.request.urlopen(req, timeout=timeout).read()
    return data if raw else json.loads(data)


def _ops(titles):
    return [t for t in titles if any(k in t.lower() for k in OPS)]


def probe_ats(slugs):
    print("=== ATS-проба (Greenhouse / Lever / Ashby) ===")
    for slug in slugs:
        hit = False
        for ats, (tpl, key, tkey) in _ATS.items():
            for variant in (slug, slug.capitalize()):
                try:
                    d = _get(tpl.format(variant))
                except urllib.error.HTTPError as e:
                    if e.code != 404:
                        print(f"  {slug:12s} {ats:10s} HTTP {e.code}")
                    continue
                except Exception:
                    continue
                jobs = d.get(key, []) if key else (d if isinstance(d, list) else [])
                if not jobs:
                    continue
                hit = True
                titles = [(j.get(tkey) or "") for j in jobs]
                ops = _ops(titles)
                ex = f"  напр: {ops[0][:40]}" if ops else ""
                verdict = "✅ ДОБАВЛЯТЬ" if len(ops) >= 3 else "⚠️ мало ops"
                print(f"  {slug:12s} {ats:10s} slug='{variant}' total={len(jobs):3d} ops={len(ops):2d}  {verdict}{ex}")
                break
        if not hit:
            print(f"  {slug:12s} — ATS не найден (greenhouse/lever/ashby пусто) → ручной/пропуск")


def probe_board(urls):
    print("=== Board-проба (забираемость + тип фида) ===")
    for url in urls:
        try:
            raw = _get(url, raw=True).decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            print(f"  ❌ {url[:50]:50s} HTTP {e.code}")
            continue
        except Exception as e:
            print(f"  ❌ {url[:50]:50s} {type(e).__name__}")
            continue
        low = raw.lower()
        feed = ("RSS/XML" if ("<rss" in low or "<feed" in low) else
                "NEXT_DATA(JSON)" if "__next_data__" in low else
                "Algolia-SPA(брайттл)" if "algolia" in low else
                "GraphQL-SPA(брайттл)" if "graphql" in low else "HTML")
        joblinks = len(set(re.findall(r'href="(/[a-z0-9-]+/[a-z0-9-]{6,})"', raw)))
        cards = len(re.findall(r'vacancy-card|job-card|JobCard|posting-', raw))
        rec = ("✅ есть структурный фид/серверный HTML — парсер надёжен"
               if feed in ("RSS/XML", "NEXT_DATA(JSON)") or cards > 20 or joblinks > 10
               else "⚠️ SPA/приватный API — парсер брайттл, лучше ручной")
        print(f"  {url[:50]:50s} {len(raw):7d}b {feed:18s} links={joblinks} cards={cards}")
        print(f"      → {rec}")


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("ats", "board"):
        sys.exit(__doc__)
    (probe_ats if sys.argv[1] == "ats" else probe_board)(sys.argv[2:])


if __name__ == "__main__":
    main()
