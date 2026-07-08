# SOURCES_AUDIT — аудит источников вакансий

> Аудит проведён 2026-07-01. config/sources.yaml пока НЕ изменён.
> После твоего «ок» — добавление на ветке `feature/sources` с диффом.

---

## 1. Что используется сейчас

### Telegram-каналы (11 шт.)
| Канал | Тематика | Язык |
|---|---|---|
| remote_jobs_ru | Remote IT/общее | RU |
| cryptoheadhunter | Crypto HR | EN |
| holder_job_devs | Web3/dev вакансии | Mixed |
| remoteweb3jobs | Remote web3 | EN |
| workers_tg | Общее | RU |
| cryptovakansii | Крипто-вакансии | RU |
| workingincrypto | Crypto jobs | EN |
| opento_crypto | Crypto | EN |
| cryptojobslist | CryptoJobsList feed | EN |
| cryptojobsh | Crypto jobs | EN |
| web3vacancy | Web3 вакансии | EN |

Интеграция: `t.me/s/{channel}` — публичный HTML-превью, без авторизации. ✓

### Web job boards (6 шт.)
| Борд | URL | Способ | Статус |
|---|---|---|---|
| remoteok | remoteok.com/api | JSON API | ✓ работает |
| cryptojobslist | cryptojobslist.com/jobs.json | JSON API | ✓ работает |
| web3career | web3.career | HTML-скрейп | ✓ работает |
| laborx | laborx.com/vacancies | HTML-скрейп | ✓ работает |
| remote3 | remote3.co/web3-jobs | HTML-скрейп | ✓ работает |
| wellfound | wellfound.com/jobs | HTML-скрейп | ✗ отключён (VPS-блок) |

### ATS — Greenhouse (8 компаний)
Публичный API: `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs`
OKX, Gemini, Fireblocks, Ripple, Consensys, Coinbase, BitGo, Bitpanda

### ATS — Lever (9 компаний)
Публичный API: `https://api.lever.co/v0/postings/{slug}?mode=json`
Binance, Celestia, MoonPay, Safe, Gauntlet, 1inch, Animoca Brands, Anchorage Digital, Kraken

> Kraken: логи показывают «0 jobs» на Lever — возможно, мигрировали на Ashby (подтверждено ниже).

### HH.ru
Официальный API, работает. ✓

---

## 2. Исследование ландшафта по категориям

### Категория 1 — Web3/крипто-нативные борды

| Площадка | Активность | Способ доступа | VPS-риск | Оценка |
|---|---|---|---|---|
| **crypto.jobs** | 3 500+ вакансий, активен 2026 | HTML-скрейп (API не найден) | Неизвестен | Средний приоритет |
| **cryptocurrencyjobs.co** | 15 000+ вакансий, авторитетный | HTML-скрейп (Substack RSS как побочный вариант) | Неизвестен | Средний приоритет |
| **cryptojobs.com** | Активен, English | HTML-скрейп | Неизвестен | Низкий (дублирует crypto.jobs) |
| **web3vacancy.com** | 2 400+ вакансий | HTML-скрейп | Неизвестен | Низкий (пересечение с web3career) |
| **thirdwork.xyz** | Нишевый, web3 talent | HTML | Неизвестен | Низкий (малый объём) |

**Вывод:** Из этой категории не хватает `cryptocurrencyjobs.co` — один из самых уважаемых EN-бордов для крипто-ролей. Но только HTML, VPS-риск неизвестен → сначала потестить.

### Категория 2 — Общие remote-борды

Уже покрыто: RemoteOK ✓. Wellfound — подтверждённый VPS-блок, не стоит.

**Jobicy** (`jobicy.com`) — имеет публичный RSS/JSON API для remote-вакансий, можно фильтровать по keywords. Низкий сигнал для крипто-ролей (генеральные remote-вакансии). Приоритет: низкий.

### Категория 3 — AI/tech борды

| Площадка | Активность | Релевантность | Вывод |
|---|---|---|---|
| **aijobs.net** | 45 000+ вакансий | Низкая — 90% ML/data science/PhD. Pre-filter отсечёт, но шум огромный | Не добавлять |
| **aijobs.ai** | Активен | Та же проблема | Не добавлять |

Для роли AI Automation (no-code/workflow) специализированных бордов с хорошим сигналом нет — это нишевое направление, вакансии размазаны по общим бордам. Крупнее всего покрывается через Lever/Greenhouse/Ashby компаний типа n8n, Zapier, Make.com.

### Категория 4 — ATS: Ashby и Workable

**Ashby** — публичный API без авторизации:
```
GET https://api.ashbyhq.com/posting-api/job-board/{slug}
```
Формат: JSON. Без ключа. VPS-безопасен (официальный эндпоинт).

Крипто-компании на Ashby (подтверждено веб-поиском):

| Компания | Slug | Релевантность для профиля |
|---|---|---|
| Ledger | `ledger` | Высокая — hardware wallets, ops/support |
| Chainlink Labs | `chainlink-labs` | Высокая — web3 infrastructure ops |
| Trust Wallet | `trust-wallet` | Высокая — wallet support/ops |
| P2P.org | `p2p.org` | Высокая — staking ops, operations роли |
| Kraken | `kraken.com` | Высокая — exchange ops/support (похоже мигрировали с Lever) |
| Allium | `allium` | Средняя — crypto data analytics |
| Flipside Crypto | `flipsidecrypto` | Средняя — crypto analytics |
| Paradigm | `Paradigm` | Портфолио VC (см. категорию 5) |

**Workable** — публичный API без авторизации:
```
GET https://apply.workable.com/api/v1/widget/accounts/{slug}
```
Формат: JSON. Без ключа. Крипто-компании на Workable — менее распространено, чем Greenhouse/Lever/Ashby. Требует отдельного исследования конкретных slug-ов. Приоритет: средний, после Ashby.

### Категория 5 — Portfolio job boards (VC-фонды)

| Фонд | URL | ATS / доступ | Вакансий | Оценка |
|---|---|---|---|---|
| **a16z crypto** | `portfoliojobs.a16z.com` + `job-boards.greenhouse.io/a16zcryptoteam` | Greenhouse! | 58+ web3 вакансий | **Добавить сейчас** — уже используем Greenhouse, просто добавить slug |
| **Paradigm** | `jobs.paradigm.xyz` | Ashby (`jobs.ashbyhq.com/Paradigm`) | 49 крипто-вакансий | **Добавить** вместе с Ashby |
| **Dragonfly** | `jobs.dragonfly.xyz` | Кастомный портал | Активен | Средний — нужно проверить HTML-структуру |
| **YC Work at a Startup** | `workatastartup.com` | Публичный API без ключа | Тысячи, но общий tech | Низкий — много шума, мало крипто-ops |
| **Multicoin / Pantera** | Нет единого портала | Разные ATS | — | Не стоит (разрознено) |

### Категория 6 — Российские/CIS-площадки

| Площадка | API / доступ | Релевантность | Оценка |
|---|---|---|---|
| **HH.ru** | Официальный API ✓ | Высокая | Уже используется ✓ |
| **Habr Career** | OAuth 2.0 + регистрация приложения через Habr | Высокая — RU IT-рынок, crypto-ops/support | Средний приоритет — нужна регистрация |
| **getmatch.ru** | API для работодателей (не для скрейпинга вакансий) | Средняя | Не стоит — нет публичного API для кандидатов |
| **geekjob.ru** | HTML + входит в агрегаторы | Низкая — мало крипто-вакансий | Не стоит |

**Habr Career:** API требует OAuth 2.0 и ручной регистрации приложения у Habr. Трудозатраты: средние. Но ценность высокая — единственная крупная RU IT-площадка за пределами HH.ru, где бывают русскоязычные crpyto-ops/support роли (особенно CIS-команды). Рекомендую добавить, но отдельным спринтом.

### Категория 7 — Telegram-каналы (недостающие)

| Канал | Подписчики | Описание | Доступ t.me/s/ |
|---|---|---|---|
| **@web3hiring** | 61 400 | Ежедневные global web3 вакансии, EN | ✓ Подтверждён |
| **@g_jobchannel** | Н/Д | getmatch IT-карьера, RU | Требует проверки |

`@web3hiring` — крупнейший из нами непокрытых каналов. 61k подписчиков против 10–20k у большинства текущих. Ежедневные посты, глобальный охват EN-вакансий. **Добавить сейчас.**

### Категория 8 — Discord

Дискорд-каналы с вакансиями (LobsterDAO, Bankless, ETHGlobal и др.) теоретически ценны, но Discord не имеет публичного API без авторизации/бота. Интеграция возможна только через официальный Bot Token + guild invite — дополнительная инфраструктура, высокие трудозатраты. Приоритет: **не сейчас**.

### Категория 9 — X/Twitter

Хэштеги #web3jobs, #cryptojobs активны, но X API v2 для поиска требует оплаты ($100+/мес) или сложного OAuth. Не рекомендую для автоматического пайплайна.

---

## 3. Таблица приоритетов (недостающие источники)

| # | Источник | Релев. | Сигнал/шум | Способ | VPS-риск | Трудозатраты | Приоритет |
|---|---|---|---|---|---|---|---|
| 1 | Ashby: Ledger, Chainlink, Trust Wallet, P2P.org, Kraken | Высокая | Высокий | Публичный JSON API | Нет (офиц.) | Низкие — шаблон есть | **Добавить сейчас** |
| 2 | Greenhouse: a16z crypto aggregator | Высокая | Высокий | Уже используем GH | Нет | Минимальные — 1 slug | **Добавить сейчас** |
| 3 | Telegram: @web3hiring | Высокая | Высокий | t.me/s/ (как текущие) | Нет | Минимальные — 1 строка | **Добавить сейчас** |
| 4 | Ashby: Paradigm portfolio | Высокая | Высокий | JSON API | Нет | Минимальные | **Добавить сейчас** |
| 5 | Habr Career | Высокая (CIS) | Средний | OAuth API | Нет | Средние — нужна регистрация | **Потом** |
| 6 | cryptocurrencyjobs.co | Средняя | Высокий | HTML-скрейп | Неизвестен | Средние | **Потом (тест VPS)** |
| 7 | Dragonfly portfolio | Средняя | Высокий | HTML | Неизвестен | Средние | **Потом** |
| 8 | YC Work at a Startup | Низкая | Низкий (шум) | Публичный API | Нет | Средние | Не сейчас |
| 9 | Workable ATS | Средняя | Средний | JSON API | Нет | Средние | Потом |

---

## 4. Шортлист: добавить сейчас

### 4.1 Новый парсер — Ashby ATS

Не требует авторизации. Паттерн аналогичен Greenhouse/Lever. Нужен новый файл `src/parsers/web/ashby.py`.

Эндпоинт: `GET https://api.ashbyhq.com/posting-api/job-board/{slug}`
Ответ: JSON `{ "jobs": [...] }`, каждый job содержит `title`, `jobUrl`, `location`, `team`, `isRemote`, `publishedAt`.

**Компании к добавлению:**
- `ledger` — Ledger (hardware wallets)
- `chainlink-labs` — Chainlink Labs
- `trust-wallet` — Trust Wallet
- `p2p.org` — P2P.org (staking operations)
- `kraken.com` — Kraken (мигрировали с Lever судя по 0 jobs)
- `Paradigm` — Paradigm (VC + portfolio aggregator)

### 4.2 Greenhouse — добавить a16z crypto

Используем существующий Greenhouse-парсер, просто добавить slug `a16zcryptoteam`.
Эндпоинт уже работает: `https://boards-api.greenhouse.io/v1/boards/a16zcryptoteam/jobs`
Это агрегирует вакансии по всему портфолио a16z crypto — ~58 живых web3-вакансий.

### 4.3 Telegram — добавить @web3hiring

Одна строка в `sources.yaml`. t.me/s/web3hiring подтверждён как публичный.

---

## 5. Черновик добавлений в формате sources.yaml

```yaml
# ── Добавить в telegram_channels ─────────────────────────────────
  - web3hiring            # 61k подписчиков, ежедневные global web3 вакансии, EN

# ── Добавить в web_job_boards → новый раздел ashby ──────────────
ashby:
  # Публичный JSON API: https://api.ashbyhq.com/posting-api/job-board/{slug}
  # Без авторизации. Формат идентичен Greenhouse (нужен отдельный парсер).
  companies:
    - name: "Ledger"
      slug: "ledger"
    - name: "Chainlink Labs"
      slug: "chainlink-labs"
    - name: "Trust Wallet"
      slug: "trust-wallet"
    - name: "P2P.org"
      slug: "p2p.org"
    - name: "Kraken (Ashby)"
      slug: "kraken.com"
    - name: "Paradigm"
      slug: "Paradigm"

# ── Добавить в greenhouse → companies ────────────────────────────
# (существующий парсер, просто ещё одна запись)
    - name: "a16z Crypto Portfolio"
      slug: "a16zcryptoteam"
```

---

## 6. Что НЕ добавлять и почему

| Источник | Причина |
|---|---|
| **Wellfound** | Подтверждённый VPS/датацентр IP-блок. Уже отключён. Без residential proxy — не работает |
| **LinkedIn** | Скрейпинг против ToS. Разрешённый путь: job alerts на email вручную |
| **aijobs.net / aijobs.ai** | Высокий шум: 90% ML-инженеры, data scientists, PhD-роли. Pre-filter отсечёт, но нагрузит лимиты AI-матчинга |
| **getmatch.ru** | Нет публичного API для вакансий. API только для работодателей (платный рекрутинг-инструмент) |
| **geekjob.ru** | Малый объём, преимущественно dev-роли. Крипто-сигнал близок к нулю |
| **crypto.jobs / cryptojobs.com** | HTML-only, нет официального API. VPS-риск неизвестен. Дублируют уже используемые борды |
| **Discord** | Требует bot token + guild invite — дополнительная инфраструктура. Не сейчас |
| **X/Twitter** | API v2 с поиском — платный ($100+/мес). Не оправдан для текущего пайплайна |
| **YC Work at a Startup** | Низкий крипто-сигнал. Преимущественно tech/dev стартапы, ops-ролей в крипто мало |
| **Habr Career** | Высокая ценность, но OAuth 2.0 + ручная регистрация у Habr — средние трудозатраты. Отложить |

---

## 7. Итог — что нужно сделать

После твоего «ок»:

1. **Новый файл** `src/parsers/web/ashby.py` — парсер Ashby API (6 компаний)
2. **Правка** `config/sources.yaml` — добавить `ashby:` секцию + `@web3hiring` в telegram_channels
3. **Правка** `src/scheduler.py` — подключить `AshbyParser` (по образцу Greenhouse/Lever)
4. **Правка** Greenhouse companies list — добавить `a16zcryptoteam`
5. Проверить `docker-compose up --build`, убедиться что новые источники дают вакансии
6. Отдельный спринт: Habr Career OAuth (когда будет время)

---

## 8. Ревизия по данным (2026-07-08) — где реально сигнал

Шортлист §4 (Ashby-парсер, a16z, @web3hiring) с 01.07 **реализован**.
Эта ревизия — не ландшафтные догадки, а **атрибуция по боевой пачке** (1891 ваканс.)
и **проба slug'ов реальным fetch'ем с датацентр-IP VPS** (сильнее веб-поиска).

### 8.1 Вклад источников в релевантные (≥45) — боевая пачка

| Источник | вакансий | из них ≥45 |
|---|---|---|
| **greenhouse** | 763 | **37** |
| **lever** | 378 | **9** |
| ashby | 85 | 2 |
| telegram (все) | ~275 | ~5 |
| hh.ru | 247 | **1** |
| remoteok | 100 | 1 |
| laborx | 38 | 1 |
| linkedin | 0 (rate-limit) | 0 |

**Вывод:** ~87% релевантных дают ATS-борды (Greenhouse/Lever/Ashby); hh/remoteok/
laborx/telegram — почти чистый шум. Самый дешёвый и сигнальный рычаг —
**не новые борды, а больше компаний в ATS-списки** (парсеры уже есть, описания
полные: 0 empty desc). Новые шумные HTML-борды (crypto.jobs и пр.) — не трогаем.

### 8.2 Проба кандидатов реальным fetch'ем (только живые, с ops-сигналом)

Эндпоинты те же, что у парсеров. `ops-like` = заголовки с ops/support/community/
compliance/treasury/custody/analyst/specialist/staking… Проверено с VPS.

| ATS | slug | вакансий | ops-like | Комментарий |
|---|---|---|---|---|
| Ashby | **Polymarket** | 55 | 12 | ★ AML/Compliance/Ops — сильнейший |
| Greenhouse | **blockchain** (Blockchain.com) | 35 | 11 | ★ биржа, много ops |
| Ashby | **stellar** (Stellar Dev Foundation) | 29 | 2 | Product Operations Lead |
| Greenhouse | **ondofinance** (Ondo) | 22 | 5 | RWA/DeFi |
| Greenhouse | **layerzerolabs** (LayerZero) | 19 | 3 | инфра |
| Ashby | **alchemy** (Alchemy) | 17 | 3 | инфра, Customer/Support |
| Ashby | **turnkey** (Turnkey) | 13 | 2 | Support Engineer |
| Greenhouse | **b2c2** (B2C2) | 9 | 1 | OTC, Compliance |
| Greenhouse | **aptoslabs** (Aptos) | 6 | 2 | Community Manager |
| Greenhouse | **figment** (Figment) | 2 | 1 | ★ staking-ops — прямо профильно |

**Опционально / низкий сигнал сейчас** (добавить можно, но пока мало ops):
Uniswap (ashby, 10/0), bitso (gh, 10/3 — LatAm/Spanish, гейт языка отсечёт),
gate (lever, 19/3 — часть ролей на китайском), swissborg (lever, 3/1),
phantom/dune/magiceden/OpenSea/matter-labs/offchainlabs (0 ops в снимке).

### 8.3 Поправки к §4 (проверено fetch'ем, не веб-поиском)

- **`chainlink-labs` (Ashby) — НЕВАЛИДЕН** (404). Рекомендация §4.1 от 01.07 была
  веб-догадкой; живой fetch её опроверг. Не добавлять под этим slug.
- Крупные (Chainalysis, Circle, Crypto.com, Bybit, KuCoin, dYdX, Galaxy,
  Wintermute) под публичными Greenhouse/Lever/Ashby slug'ами **не разрешились** —
  сидят на Workday/кастомных порталах, текущим пайплайном не забираются. Не цель.

### 8.4 Черновик добавлений (в существующие парсеры, нового кода НЕ нужно)

```yaml
# settings.yaml → parsers.greenhouse.companies (+6)
  - { slug: "blockchain",     name: "Blockchain.com" }
  - { slug: "ondofinance",    name: "Ondo Finance" }
  - { slug: "layerzerolabs",  name: "LayerZero" }
  - { slug: "aptoslabs",      name: "Aptos" }
  - { slug: "b2c2",           name: "B2C2" }
  - { slug: "figment",        name: "Figment" }        # staking-ops

# settings.yaml → parsers.ashby.companies (+4)
  - { slug: "Polymarket",     name: "Polymarket" }
  - { slug: "stellar",        name: "Stellar" }
  - { slug: "alchemy",        name: "Alchemy" }
  - { slug: "turnkey",        name: "Turnkey" }
```

Итого +10 компаний в ATS-списки (~180 доп. вакансий/прогон из высокосигнального
сегмента). Telegram/hh/remoteok — не расширяем (шум). LinkedIn rate-limit на
датацентр-IP — отдельная проблема, не источниковая.
