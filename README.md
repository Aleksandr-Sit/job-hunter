# Job Hunter Bot 🤖

Automated job search system for Web3/DeFi operations roles. Parses 10+ sources every hour, filters with AI, sends relevant vacancies to Telegram.

## How it works

```
Sources (1000+ jobs/run)
    │
    ▼
Pre-filter — removes senior/director/dev roles, 6+ years exp, coding requirements
    │
    ▼
AI Matching — Cerebras LLM scores each job 0–100 against your profile
    │
    ▼
Telegram — sends only jobs with score ≥ 65, sorted by relevance
```

## Sources

| Source | Type | Jobs/run |
|--------|------|----------|
| OKX, Coinbase, Consensys, Ripple, Gemini, Fireblocks | Greenhouse API | ~400 |
| Kraken, Celestia | Lever API | ~10 |
| HH.ru | RSS (7 queries) | ~90 |
| RemoteOK | JSON API | ~100 |
| CryptoJobsList, LaborX, Remote3 | Web scraping | ~80 |
| 10 Telegram channels | t.me/s/ scraping | ~190 |

## AI Matching

Each job is scored against the candidate profile (resume + skills + preferences) using **Cerebras** inference (gpt-oss-120b model). Batch processing: 5 jobs per request.

Scoring:
- **90–100** — perfect match
- **75–89** — strong match, 1–2 minor gaps  
- **65–74** — decent match, worth applying
- **< 65** — filtered out

Checkpoint saved after every batch → safe to restart mid-run without re-processing.

## Pre-filter rules

Automatically excluded:
- Senior / Director / Head / VP / C-level titles
- Developer / Engineer roles (unless QA/Ops context)
- Requires Solidity, smart contract development, backend coding
- 6+ years experience requirement
- Fluent/Native/C1/C2 English requirement
- Non-Russian/English language requirements

## Telegram notification format

```
🎯 Crypto Operations Manager — 87/100

🏢 Binance
💰 $3000–5000 / мес · Remote
📍 cryptojobslist.com

Why fits:
• CEX/DEX operations matches core experience
• Binance/OKX tools required — direct match

Watch out:
• Team lead responsibilities mentioned

Apply highlighting CEX trading ops and on-chain monitoring experience.

🔗 Open vacancy
```

## Setup

**1. Clone and install**
```bash
git clone https://github.com/Aleksandr-Sit/job-hunter.git
cd job-hunter
pip install -r requirements.txt
```

**2. Configure**
```bash
cp .env.example .env
# Edit .env — add your API keys
```

Required keys in `.env`:
```env
CEREBRAS_API_KEY=csk-...        # inference.cerebras.ai — free, no card
CEREBRAS_MODEL=gpt-oss-120b

TELEGRAM_BOT_TOKEN=...          # @BotFather
TELEGRAM_CHAT_ID=...            # @userinfobot

# Optional: HH.ru works without keys (public RSS)
```

**3. Edit your profile**

- `config/profile/resume.md` — your resume
- `config/profile/skills.json` — skills and levels
- `config/profile/preferences.json` — target roles, salary, stack

**4a. Run natively**
```bash
python -m src.scheduler
```

**4b. Run with Docker**
```bash
docker compose up -d
docker compose logs -f   # watch logs
```

> **Important — switching from native to Docker:** If you previously ran the bot natively, SQLite may have left `data/jobs.db-wal` and `data/jobs.db-shm` files. These cause a `disk I/O error` inside Docker. Before the first `docker compose up`, stop any running Python processes and delete those two files if they exist.

Runs every 60 minutes. Restarts automatically on failure (`restart: unless-stopped`).

## Auto-start on Windows

The included `start-job-hunter.bat` can be added to Windows Task Scheduler or Startup folder to run automatically on PC start.

## Project structure

```
job-hunter/
├── config/
│   ├── settings.yaml          # intervals, threshold, sources
│   ├── sources.yaml           # Telegram channels, job boards
│   └── profile/               # your resume, skills, preferences
├── src/
│   ├── parsers/               # HH.ru, Telegram, Greenhouse, Lever, web boards
│   ├── matcher/
│   │   ├── gemini_matcher.py  # Cerebras AI batch matching
│   │   └── pre_filter.py      # fast keyword filter before AI
│   ├── bot/                   # Telegram notifications
│   ├── storage.py             # SQLite: dedup + match cache
│   └── scheduler.py           # APScheduler main loop
├── data/                      # SQLite DB, logs, match checkpoints (gitignored)
├── .env.example
└── requirements.txt
```

## Tech stack

- **Python 3.11+**
- **Cerebras** — LLM inference (free tier, gpt-oss-120b)
- **APScheduler** — job scheduling
- **python-telegram-bot** — Telegram notifications
- **BeautifulSoup4 + requests** — web scraping
- **SQLite** — dedup and match result cache
