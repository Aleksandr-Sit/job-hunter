# Job Hunter Bot 🤖

Automated job search system for Web3/DeFi operations roles. Parses 10+ sources every hour, filters with AI, sends relevant vacancies to Telegram.

## How it works

```
Sources (1600+ jobs/run)
    │
    ▼
Pre-filter — removes senior/director/dev roles, 6+ years exp, coding requirements
    │
    ▼
AI Matching — Cerebras LLM scores each job 0–100 against your profile
    │
    ▼
Telegram — sends only jobs with score ≥ 65, in Russian, sorted by relevance
```

## Sources

| Source | Type | Jobs/run |
|--------|------|----------|
| OKX, Coinbase, Ripple, Gemini, Fireblocks, Consensys, BitGo, Bitpanda | Greenhouse API | ~700 |
| Binance, Kraken, Anchorage Digital, MoonPay, Safe, Gauntlet, 1inch, Animoca Brands, Celestia | Lever API | ~460 |
| HH.ru | REST API (12 queries) | ~120 |
| RemoteOK | JSON API | ~100 |
| CryptoJobsList, LaborX, Remote3 | Web scraping | ~100 |
| 11 Telegram channels | t.me/s/ scraping | ~210 |

## AI Matching

Each job is scored against the candidate profile (resume + skills + preferences) using **Cerebras** inference (gpt-oss-120b model). Batch processing: 5 jobs per request.

Scoring:
- **90–100** — perfect match
- **75–89** — strong match, 1–2 minor gaps  
- **65–74** — decent match, worth applying
- **< 65** — filtered out

Checkpoint saved after every batch → safe to restart mid-run without re-processing.

## Pre-filter rules

Two-layer filtering before AI matching (`config/criteria.yaml`):

**Hard gate** (instant exclude):
- C-level / founder / president titles
- Pure dev roles (Solidity, smart contracts, backend/frontend coding)
- Non-Russian/English language requirements

**Weighted score** (0–100) — soft penalties, lower the score but don't exclude:
- Director / Head / VP / Lead / Principal titles
- Fluent/Native/C1/C2 English requirement (penalty weight varies by role)
- 6+ years experience requirement

Only jobs that pass the gate **and** clear the role's threshold go to AI matching.

## Telegram notification format

```
🎯 Crypto Operations Manager
Binance  ·  Remote  ·  💰 3000–5000 USDT

──────────────────────

✅ Почему подходит
· Опыт с CEX/DEX операциями — прямое совпадение
· Знание инструментов Binance и OKX

──────────────────────

⚠️ Учесть
· Упоминаются обязанности тимлида

──────────────────────

💬 Укажи опыт торговых операций на CEX и мониторинга on-chain активности.

──────────────────────

87/100  ·  cryptojobslist.com
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

## Deploy to VPS (recommended)

Running on a VPS means the bot works 24/7 without your PC being on.

**Requirements:** Ubuntu 22.04+, Docker, SSH access.

```bash
# 1. Install Docker on server
curl -fsSL https://get.docker.com | sh && systemctl enable docker

# 2. Clone repo
git clone https://github.com/Aleksandr-Sit/job-hunter.git /opt/job-hunter
mkdir -p /opt/job-hunter/data/logs

# 3. Copy secrets from local machine (run in local PowerShell)
scp -i "~/.ssh/your_key" .env root@<SERVER_IP>:/opt/job-hunter/
scp -i "~/.ssh/your_key" data/tg_session.session root@<SERVER_IP>:/opt/job-hunter/data/
scp -i "~/.ssh/your_key" data/jobs.db root@<SERVER_IP>:/opt/job-hunter/data/

# 4. Start
cd /opt/job-hunter && chmod 600 .env && docker compose up -d --build
```

After deploy the container restarts automatically on server reboot (`restart: unless-stopped`).

## Project structure

```
job-hunter/
├── PROFILE.md                 # master candidate profile (source of truth)
├── CLAUDE.md                  # project rules for AI-assisted dev
├── config/
│   ├── settings.yaml          # intervals, threshold, sources
│   ├── sources.yaml           # Telegram channels, job boards
│   ├── criteria.yaml          # role-scoring criteria (weights, keywords)
│   └── profile/               # your resume, skills, preferences
├── src/
│   ├── parsers/                # HH.ru, Telegram, Greenhouse, Lever, web boards
│   ├── matcher/
│   │   ├── cerebras_matcher.py # Cerebras AI batch matching
│   │   └── pre_filter.py      # gate + weighted scoring before AI
│   ├── bot/                    # Telegram notifications
│   ├── storage.py              # SQLite: dedup + match cache
│   └── scheduler.py            # APScheduler main loop
├── docs/                       # reference docs, target criteria, audit report
├── data/                       # SQLite DB, logs, match checkpoints (gitignored)
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
