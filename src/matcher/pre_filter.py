import json
import re
from pathlib import Path
from ..models import Job

_PROFILE_DIR = Path(__file__).parent.parent.parent / "config" / "profile"

# ── 1. Должности, исключаемые по заголовку ────────────────────────────────────
# Высокие позиции
_EXCLUDED_TITLE_WORDS = {
    "senior", "ceo", "chief", "director", "директор", "trader", "трейдер",
    "руководитель", "head", "vp", "principal", "president",
    "cto", "cfo", "coo", "cmo", "founder", "co-founder",
    # Dev-роли — пишем код, не операции
    "solidity", "backend", "frontend", "fullstack", "full-stack",
    "auditor",
    # Финансовые роли без Web3-операций
    "quant", "quantitative",
}

# Dev-роли — слова в ЗАГОЛОВКЕ, которые указывают на разработчика,
# НО допустимы если рядом стоит qa/test/operations/support/community
_DEV_TITLE_WORDS = {"developer", "programmer", "engineer", "devops", "architect"}
# Только конкретные функциональные роли, НЕ доменные слова (web3/blockchain/crypto)
_OPS_QA_TITLE_WORDS = {
    "qa", "test", "testing", "tester", "operations", "ops",
    "support", "community", "relations",
}

# Финансовые роли (только если в заголовке — без web3/crypto контекста)
_FINANCE_TITLE_WORDS = {"portfolio", "fund", "quant", "quantitative"}

# ── 2. Требования писать код (в описании) ─────────────────────────────────────
# Ловит требования разработки, а не использования инструментов
_CODE_REQUIRED_PATTERN = re.compile(
    r'(?:'
    # Автотестирование (инструменты разработчика-QA)
    r'\b(?:selenium|cypress|playwright|pytest|jest|mocha|appium|robot\s+framework)\b'
    # DevOps как требование
    r'|\b(?:kubernetes|k8s|helm|terraform|ansible|ci/cd|jenkins|github\s+actions)\s+(?:experience|required|skills?|знани)'
    # Написание кода на языках (именно разработка)
    r'|\b(?:write|develop|code|program|implement|build)\b.{0,40}\b(?:python|javascript|typescript|rust|golang|go|solidity|java|c\+\+)\b'
    r'|\b(?:python|javascript|typescript|rust|golang|solidity)\b.{0,40}\b(?:developer|development|programming|coding|написани)\b'
    # Smart contract разработка (не взаимодействие)
    r'|\bsmart\s+contract\s+(?:develop|creat|writ|build|deploy(?:ment)?)\b'
    r')',
    re.IGNORECASE,
)

# ── 3. Опыт 7+ лет ────────────────────────────────────────────────────────────
_HIGH_EXP_PATTERN = re.compile(
    r'(?:'
    r'\b(?:7|8|9|1\d|20)\+?\s*(?:years?|лет|года?|г\.)'
    r'|(?:more\s+than|более|свыше|over)\s+6\s*(?:years?|лет)'
    r'|(?:from|от|min(?:imum)?\.?\s*)\s*[7-9]\d*\s*(?:years?|лет)'
    r')',
    re.IGNORECASE,
)

# ── 4. Высокий уровень английского ────────────────────────────────────────────
# A2/B1 — фильтруем fluent/native/C1/C2/upper-intermediate как требование
_HIGH_ENGLISH_PATTERN = re.compile(
    r'(?:'
    r'\b(?:fluent|native|proficient|perfect)\s+english\b'
    r'|\benglish\s*[:\-–]?\s*(?:fluent|native|c[12]|upper[- ]intermediate|advanced)\b'
    r'|\b(?:c[12]|upper[- ]intermediate|advanced)\s+(?:level\s+)?english\b'
    r'|\benglish\s+(?:level\s+)?(?:c[12]|upper[- ]intermediate)\b'
    r')',
    re.IGNORECASE,
)

# ── 5. Иностранные языки (кроме ru/en) ────────────────────────────────────────
_FOREIGN_LANG_PATTERN = re.compile(
    r'\b(chinese|mandarin|deutsch|german|french|français|spanish|español|'
    r'japanese|korean|portuguese|arabic|hindi|italian|dutch|turkish|'
    r'польский|немецкий|французский|испанский|китайский|японский|'
    r'корейский|арабский|итальянский|турецкий)\b',
    re.IGNORECASE,
)


def _load_keywords() -> tuple[set[str], set[str]]:
    prefs = json.loads((_PROFILE_DIR / "preferences.json").read_text(encoding="utf-8"))
    skills_raw = json.loads((_PROFILE_DIR / "skills.json").read_text(encoding="utf-8"))

    include: set[str] = set()

    for role in prefs.get("roles", []):
        include.update(role.lower().split())
    for kw in prefs.get("tech_stack_must", []):
        include.add(kw.lower())
    for kw in prefs.get("tech_stack_nice", []):
        include.add(kw.lower())
    for ind in prefs.get("industries_preferred", []):
        include.update(w for w in ind.lower().split("/") if len(w) > 2)

    for key in ("wallets", "networks", "defi_protocols", "exchanges", "analytics_tools", "domains"):
        for item in skills_raw.get(key, []):
            if isinstance(item, str):
                include.add(item.lower())

    include.update([
        "web3", "blockchain", "defi", "crypto", "nft", "dao", "dex", "cex",
        "wallet", "onchain", "on-chain", "ethereum", "solana", "evm",
        "operations", "ops", "qa", "tester", "testing", "support",
        "community", "discord", "airdrop", "protocol", "dapp",
        "ai", "automation", "n8n", "workflow",
    ])

    avoid = {i.lower() for i in prefs.get("industries_avoid", [])}
    return include, avoid


INCLUDE_KW, AVOID_KW = _load_keywords()


def passes_pre_filter(job: Job) -> bool:
    text = f"{job.title} {job.description}".lower()
    title_tokens = set(re.findall(r'[\w-]+', job.title.lower()))

    # Отраслевые стоп-слова из профиля
    if any(kw in text for kw in AVOID_KW):
        return False

    # Высокие должности и dev-роли (по заголовку)
    if title_tokens & _EXCLUDED_TITLE_WORDS:
        return False

    # developer/engineer/architect допустимы только с qa/ops/web3 контекстом
    if title_tokens & _DEV_TITLE_WORDS:
        if not (title_tokens & _OPS_QA_TITLE_WORDS):
            return False

    # Финансовые роли без web3-контекста
    if title_tokens & _FINANCE_TITLE_WORDS:
        if not (title_tokens & {"web3", "crypto", "blockchain", "defi", "digital"}):
            return False

    # Требования писать код
    if _CODE_REQUIRED_PATTERN.search(text):
        return False

    # Опыт 7+ лет
    if _HIGH_EXP_PATTERN.search(text):
        return False

    # Высокий уровень английского
    if _HIGH_ENGLISH_PATTERN.search(text):
        return False

    # Требования иностранного языка (кроме ru/en)
    if _FOREIGN_LANG_PATTERN.search(text):
        return False

    tokens = set(re.findall(r'[\w-]+', text))
    return bool(tokens & INCLUDE_KW)
