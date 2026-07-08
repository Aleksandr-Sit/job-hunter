# TARGET_CRITERIA — критерии целевых вакансий (Этап 3)

Две приоритетные роли. Это источник правды для фильтра. Эталон логики и списков — в `target_criteria_REFERENCE.py` (тот же каталог).

Базовая честная установка: **английский A1–A2 — реальный барьер.** Поэтому требование сильного английского снижает балл вакансии, а русскоязычные/CIS-команды — повышают. Для Support английский весит сильнее, чем для Ops: Support — про коммуникацию, и тут реалистичная цель — поддержка в русскоязычных/CIS крипто-проектах или текстовая/async-поддержка, а не голосовая на английском.

---

## РОЛЬ A — Crypto / Web3 / DeFi Operations *(основная, самая сильная)*

**Целевые должности (точно/близко):** Crypto Operations, Web3 Operations, DeFi Operations, Blockchain Operations, Digital Asset Operations, Exchange Operations, Trading/Treasury/Settlement Operations, On-chain Operations, Operations Specialist/Associate *(в крипто-контексте)*.

**Смежные (ниже вес):** Operations Analyst/Coordinator, Payments Operations, KYC/Compliance Operations, Operations Manager.

**Обязательно (иначе вакансия не та):** есть слово из {operations / ops / operational} **И** крипто-термин {crypto, blockchain, web3, defi, on-chain, exchange, wallet, token, stablecoin, CEX, DEX, …}.

**Повышают балл:** multichain, EVM, Ethereum/Solana/Arbitrum, staking, bridge, liquidity, settlement, reconciliation, custody, treasury, transaction monitoring, KYC/AML, market maker, async, remote.

**Понижают балл:** sales, account executive, business development, cold calling, phone support; лидерские — head of, director, VP, principal, lead, chief; мягко — senior.

**Формат:** remote (приоритет) / релокация (Кипр, Греция, Таиланд, Турция) / гибрид. Только-офис в неподходящей локации — сильный минус.

**Зарплата:** порог 150 000 ₽ (≈ $1 800/мес). Крипто-нативные роли часто платят в USD/стейблкоинах — это плюс под РФ.

**Английский:** в идеале B1+, но для Ops повседневно решает меньше. Требование C1/native — минус, но не блок.

**Красные флаги (блок):** предоплата/«пришлите крипто для теста», recruitment/registration fee, неоплата/volunteer only/equity only, чисто-разработческие вакансии (Solidity/Rust/Smart-contract/Software Engineer).

---

## РОЛЬ B — Web3 / Crypto Support *(вторая, но английский — узкое место)*

**Целевые должности:** Web3/Crypto Support, Customer/Technical/User Support *(крипто)*, Community Support, Wallet/Exchange Support, Support Specialist/Agent, Trust & Safety.

**Смежные:** Customer Success/Experience, Help Desk, Support Engineer (Tier 1/2), Compliance/KYC Support, Onboarding Specialist.

**Обязательно:** слово из {support / customer / help desk / user support / community / trust & safety} **И** крипто-термин.

**Повышают балл:** Zendesk, Intercom, Freshdesk, Discord, Telegram, troubleshooting, CRM, SLA, wallet, transaction, knowledge base, async, ticket; **сильно** — Russian-speaking / CIS / RU community.

**Понижают балл (сильно):** phone/voice support, call center *(живой английский голосом)*, native/fluent English / C1 / C2; продажи — outbound, cold calling, quota, upsell; лидерские.

**Формат:** remote (приоритет), сменный график — норма. Релокация — те же страны.

**Зарплата:** обычно ниже Ops; порог тот же 150 000 ₽, но допускай чуть ниже для входа.

**Английский — ключевой фактор.** Реалистично: русскоязычные/CIS крипто-проекты, текстовая/тикетная/Discord-поддержка. Голосовая англоязычная поддержка — не цель на текущем уровне.

**Красные флаги:** как у Роли A.

---

## Уровень и сегмент — приоритет entry/junior *(решение консультации 08.07.2026)*

Профиль кандидата на 08.07: подушка 6–8 мес, готов на меньший старт в компании,
**которая обучает и вкладывает**, оплата в USDT/крипте — принимает. Отсюда:

- **Приоритетный сегмент — entry/junior/associate/trainee** в обеих ролях. Это
  самый конверсионный вход (низкий барьер, «обучим на месте»), а 6 лет практики
  DeFi — сильный дифференциатор против других джуниоров.
- **`entry_boost` (+8):** junior, entry-level, graduate, trainee, no experience,
  will train, training provided, apprentice, младший, стажёр, без опыта. Только
  повышает балл — гейт не трогает (ops/support уже гарантирован must_role), поэтому
  mid-level роли не исключаются, просто entry всплывает выше.
- **Оплата в крипте/USDT** — плюс под РФ (крипто-нативные контрактер-роли обходят
  платёжную проблему). Как отдельное стоп/буст-слово НЕ вводим — в описаниях почти
  не указывают, это шум; учитывается стратегией отклика, не фильтром.
- **P2P/OTC-опыт** кандидата — личный масштаб (до ~500к ₽, для себя и знакомых),
  **не** продаётся как OTC/settlement desk. В фильтре не заголовок, а поддерживающий
  сигнал (понимание fiat-рельсов).
- **Английский** — письменный async, прокачка фоном 20–30 мин/день; voice/созвоны
  вне цели. Правила штрафа за сильный английский (выше) остаются.

Вне доступа сейчас (не целимся): senior ops/BD/PM в топ-международке (гейт fluent
English), голосовая поддержка, чистый dev.

---

## Рубрика для LLM-скоринга (Claude API)

Вставь в системный промпт AI-матчинга, чтобы LLM-слой совпадал с правилами:

```
Оцени вакансию для кандидата (профиль — в PROFILE.md / config/profile) по шкале 0–100.
Кандидат: ~5 лет практического crypto/web3 on-chain опыта (операции, кошельки,
биржи, мультичейн), фон в поддержке/продажах, осваивает AI-автоматизацию.
Английский A1–A2. Хочет remote или релокацию (Кипр/Греция/Таиланд/Турция).

Ставь высокий балл, если это Crypto/Web3/DeFi Operations или Web3/Crypto Support
уровня specialist/associate, remote или релокация подходит, и не требуется
сильный английский.

Снижай балл за: требование fluent/native English или C1/C2 (для Support — сильнее);
голосовую/телефонную поддержку; продажи; лидерские роли (Head/Director/Lead/VP);
только-офис в неподходящей стране.

Ставь 0, если это чисто разработческая роль (Solidity/Rust/Smart-contract/
Software Engineer), неоплата, скам, или не крипто-область.

Russian-speaking / CIS команда — заметный плюс (особенно для Support).
Верни JSON: {"score": int, "role": "crypto_ops|web3_support|none", "reason": "..."}.
```

---

## Как встроить в текущий проект (Docker + YAML + src/matcher/pre_filter.py)

**Не копируй `target_criteria_REFERENCE.py` как отдельный модуль** — это дубль и конфликт с уже работающим `src/matcher/pre_filter.py`. Перенеси из него критерии и логику в твою структуру:

1. **Критерии двух ролей** → в YAML (`config/settings.yaml` или новый `config/criteria.yaml`), в стиле существующих конфигов: целевые должности, must/boost/стоп-слова, штраф за сильный английский и за продажи/лидерство, пороги.
2. **Логику** (gate-предфильтр + взвешенный балл + `reasons`) → в `src/matcher/pre_filter.py`, **переиспользуя уже сделанную CIS/русскоязычную часть, не пересоздавая её.**
3. **LLM-рубрику** (выше) → в промпт AI-матчинга.
4. **`reasons`** → выводи в карточку Telegram, чтобы на глаз видеть, почему вакансия прошла.

Делать это безопаснее всего через Claude Code на отдельной ветке — готовый промпт лежит в `docs/MERGE_TASK.md`.

> Списки — живые. Поймал нерелевантную вакансию — добавь её слово в стоп-слова; пропустил хорошую — посмотри, какой гейт её срезал.
