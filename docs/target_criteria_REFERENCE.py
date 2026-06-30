# ============================================================================
# СПРАВОЧНИК / REFERENCE ONLY — НЕ ПОДКЛЮЧАТЬ КАК МОДУЛЬ ПАЙПЛАЙНА.
# Это эталон критериев и логики для двух ролей. Claude Code переносит списки
# в config/ (YAML) и логику в src/matcher/pre_filter.py, переиспользуя уже
# сделанную CIS/русскоязычную доработку. Этот файл импортировать/запускать
# в боте НЕ нужно — он лежит в docs/ как ориентир.
# Запустить как демо можно: python target_criteria_REFERENCE.py
# ============================================================================
"""
Критерии целевых вакансий (эталон).

Роли:
  - "crypto_ops"    : Crypto / Web3 / DeFi Operations
  - "web3_support"  : Web3 / Crypto Support

Логика двухслойная:
  1) passes_hard_gates() — дешёвый фильтр: мгновенно отсекает мусор
     (скам, неоплата, чисто-разработческие роли, не та доменная область).
  2) score_vacancy() — взвешенный балл 0..100 + причины.

Английский A1–A2 зашит в скоринг: требование сильного английского штрафует балл
(для Support — сильнее), русскоязычные/CIS-команды дают бонус.
"""

import re

# ── Глобальные стоп-слова: reject для ЛЮБОЙ роли ──────────────────────────────
GLOBAL_HARD_EXCLUDE = [
    # неоплата
    "unpaid", "volunteer only", "equity only", "no salary", "for free",
    # скам-сигналы
    "recruitment fee", "registration fee", "processing fee", "pay to apply",
    "send funds", "send crypto", "upfront payment", "training fee",
    # чисто разработческие роли (нужен код, которого пока нет)
    "solidity developer", "smart contract developer", "smart contract engineer",
    "rust developer", "protocol engineer", "blockchain developer",
    "frontend developer", "front-end developer", "backend developer",
    "back-end developer", "full stack developer", "full-stack developer",
    "software engineer", "staff engineer", "devops engineer",
]

# ── Критерии по ролям ─────────────────────────────────────────────────────────
ROLES = {
    "crypto_ops": {
        "threshold": 55,
        "english_weight": 1.0,  # для ops английский менее критичен в ежедневной работе
        "must_role": ["operations", "ops", "operational"],
        "must_domain": [
            "crypto", "blockchain", "web3", "defi", "on-chain", "onchain",
            "digital asset", "stablecoin", "cex", "dex", "exchange", "wallet",
            "token", "ledger", "etherscan",
        ],
        "titles_strong": [
            "crypto operations", "web3 operations", "defi operations",
            "blockchain operations", "digital asset operations",
            "exchange operations", "trading operations", "treasury operations",
            "settlement operations", "on-chain operations", "token operations",
            "wallet operations", "crypto ops", "operations specialist",
            "operations associate",
        ],
        "titles_weak": [
            "operations analyst", "business operations", "operations coordinator",
            "payment operations", "payments operations", "compliance operations",
            "kyc operations", "operations manager",
        ],
        "keywords_boost": [
            "multichain", "evm", "ethereum", "solana", "arbitrum", "staking",
            "bridge", "liquidity", "settlement", "reconciliation", "custody",
            "treasury", "metamask", "transaction monitoring", "market maker",
            "kyc", "aml", "trading", "async",
        ],
        "keywords_penalize": [
            "sales", "account executive", "business development", "cold calling",
            "phone support", "head of", "director", "vp of", "vice president",
            "principal", "lead", "chief",
        ],
        "senior_terms": ["senior", "sr."],
    },

    "web3_support": {
        "threshold": 55,
        "english_weight": 1.6,  # Support — коммуникационная роль, английский решает
        "must_role": [
            "support", "customer", "help desk", "helpdesk", "trust and safety",
            "trust & safety", "user support", "community",
        ],
        "must_domain": [
            "crypto", "blockchain", "web3", "defi", "on-chain", "onchain",
            "digital asset", "stablecoin", "cex", "dex", "exchange", "wallet",
            "token", "ledger", "metamask",
        ],
        "titles_strong": [
            "web3 support", "crypto support", "customer support",
            "technical support", "user support", "community support",
            "wallet support", "exchange support", "support specialist",
            "support agent", "support representative", "trust and safety",
            "trust & safety",
        ],
        "titles_weak": [
            "customer success", "customer experience", "client support",
            "help desk", "helpdesk", "support engineer", "tier 1", "tier 2",
            "compliance support", "onboarding specialist", "kyc",
        ],
        "keywords_boost": [
            "zendesk", "intercom", "freshdesk", "discord", "telegram",
            "troubleshooting", "crm", "sla", "wallet", "transaction",
            "knowledge base", "documentation", "multichain", "on-chain",
            "async", "ticket",
        ],
        "keywords_penalize": [
            "sales", "account executive", "business development", "outbound",
            "cold calling", "quota", "upsell", "phone support", "voice support",
            "call center", "head of", "director", "lead", "chief",
        ],
        "senior_terms": ["senior", "sr."],
    },
}

# ── Общие списки (одинаковы для ролей) ────────────────────────────────────────
ENGLISH_PENALTY = [
    "native english", "native-level english", "fluent english",
    "fluent in english", "excellent english", "perfect english",
    "strong command of english", "c1", "c2", "english c1", "english c2",
]
ENGLISH_BOOST = ["russian", "russian-speaking", "русск", "cis", "снг", "ru community"]
REMOTE_BOOST = [
    "remote", "fully remote", "work from anywhere", "anywhere", "worldwide",
    "distributed team", "work from home",
]
ONSITE_PENALTY = ["on-site", "onsite", "on site", "in office", "in-office"]
RELOCATION_OK = [
    "cyprus", "limassol", "greece", "athens", "thailand", "turkey", "istanbul",
    "кипр", "лимассол", "греция", "таиланд", "турция",
]

# ── Веса ──────────────────────────────────────────────────────────────────────
W = {
    "gate_base": 10,
    "title_strong": 40,
    "title_weak": 20,
    "boost_each": 6, "boost_cap": 30,
    "penalize_each": -12, "penalize_cap": -36,
    "senior": -6,
    "remote": 10,
    "relocation": 8,
    "onsite": -18,
    "english_penalty_each": -14,
    "english_boost": 10,
}


def _matches(term, text):
    """Слово целиком для простых токенов, подстрока — для фраз со спецсимволами."""
    if re.fullmatch(r"[a-zа-я0-9.\- ]+", term) and " " not in term and "." not in term:
        return re.search(r"\b" + re.escape(term) + r"\b", text) is not None
    return term in text


def _hits(terms, text):
    found = [t for t in terms if _matches(t, text)]
    return len(found), found


def _n(s):
    return (s or "").lower()


def passes_hard_gates(title, text, role_key):
    """Дешёвый предфильтр. Возвращает (bool, [причины])."""
    role = ROLES[role_key]
    blob = f"{_n(title)} {_n(text)}"

    for t in GLOBAL_HARD_EXCLUDE:
        if _matches(t, blob):
            return False, [f"hard-exclude: '{t}'"]

    n_role, _ = _hits(role["must_role"], blob)
    n_dom, _ = _hits(role["must_domain"], blob)
    if n_role == 0:
        return False, ["нет ролевых ключевых слов (не та функция)"]
    if n_dom == 0:
        return False, ["нет крипто/web3 контекста (не та область)"]
    return True, ["gate ok"]


def score_vacancy(title, text, role_key):
    """Балл 0..100 + рекомендация + причины. Сначала прогоняет гейты."""
    role = ROLES[role_key]
    ok, reasons = passes_hard_gates(title, text, role_key)
    if not ok:
        return {"role": role_key, "passed_gate": False, "score": 0,
                "recommend": False, "reasons": reasons}

    t = _n(title)
    blob = f"{t} {_n(text)}"
    score = W["gate_base"]
    reasons = []

    # заголовок
    if _hits(role["titles_strong"], t)[0] or _hits(role["titles_strong"], blob)[0]:
        score += W["title_strong"]; reasons.append("сильное совпадение по должности")
    elif _hits(role["titles_weak"], blob)[0]:
        score += W["title_weak"]; reasons.append("смежная должность")
    else:
        reasons.append("должность напрямую не совпала")

    # бусты
    nb, fb = _hits(role["keywords_boost"], blob)
    if nb:
        add = min(nb * W["boost_each"], W["boost_cap"])
        score += add; reasons.append(f"+{add} релевантные навыки: {', '.join(fb[:6])}")

    # штрафы (продажи/лидерство/телефон)
    npz, fpz = _hits(role["keywords_penalize"], blob)
    if npz:
        sub = max(npz * W["penalize_each"], W["penalize_cap"])
        score += sub; reasons.append(f"{sub} нерелевантные/несоответствующие: {', '.join(fpz[:6])}")

    # senior
    if _hits(role["senior_terms"], t)[0]:
        score += W["senior"]; reasons.append("senior-уровень (мягкий штраф)")

    # формат
    reloc = _hits(RELOCATION_OK, blob)[0]
    remote = _hits(REMOTE_BOOST, blob)[0]
    onsite = _hits(ONSITE_PENALTY, blob)[0]
    if remote:
        score += W["remote"]; reasons.append("remote")
    if reloc:
        score += W["relocation"]; reasons.append("страна релокации подходит")
    if onsite and not remote and not reloc:
        score += W["onsite"]; reasons.append("только офис в неподходящей локации")

    # английский (с весом роли)
    ew = role["english_weight"]
    nep, fep = _hits(ENGLISH_PENALTY, blob)
    if nep:
        sub = int(nep * W["english_penalty_each"] * ew)
        score += sub; reasons.append(f"{sub} требуется сильный английский: {', '.join(fep[:4])}")
    if _hits(ENGLISH_BOOST, blob)[0]:
        add = int(W["english_boost"] * ew)
        score += add; reasons.append(f"+{add} русскоязычная/CIS команда")

    score = max(0, min(100, score))
    return {
        "role": role_key,
        "passed_gate": True,
        "score": score,
        "recommend": score >= role["threshold"],
        "reasons": reasons,
    }


def classify(title, text):
    """Скорит вакансию по обеим ролям и возвращает лучшую."""
    results = [score_vacancy(title, text, r) for r in ROLES]
    best = max(results, key=lambda r: r["score"])
    return {"best": best, "all": results}


if __name__ == "__main__":
    samples = [
        ("Crypto Operations Specialist (Remote, Global)",
         "We need someone with hands-on multichain experience: wallets, on-chain "
         "transactions, settlement and reconciliation across CEX and DEX. Stablecoin "
         "payouts. Fully remote, distributed team."),
        ("Senior Solidity Developer",
         "Build smart contracts in Solidity, audit protocols. On-site New York."),
        ("Customer Support Specialist - SaaS",
         "Handle tickets for our marketing SaaS. Zendesk experience required."),
        ("Web3 Customer Support Agent",
         "Phone support for our exchange users. Native English required. Call center."),
        ("Crypto Support Agent — Russian-speaking community",
         "Help users with wallet and transaction issues in Discord/Telegram. "
         "Russian-speaking community. Remote, async, Zendesk."),
        ("DeFi Operations Lead",
         "Lead on-chain operations, staking and liquidity. On-site, fluent English (C1)."),
    ]
    for title, text in samples:
        res = classify(title, text)["best"]
        print(f"[{res['score']:3d}] reco={res['recommend']!s:5} "
              f"role={res['role']:13} | {title}")
        for r in res["reasons"]:
            print(f"        - {r}")
        print()
