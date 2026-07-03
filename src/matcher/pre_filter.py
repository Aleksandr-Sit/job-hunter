import json
import re
from pathlib import Path

import yaml

from ..models import Job

_PROFILE_DIR = Path(__file__).parent.parent.parent / "config" / "profile"
_CRITERIA_FILE = Path(__file__).parent.parent.parent / "config" / "criteria.yaml"

# ── 1. Должности, исключаемые по заголовку ────────────────────────────────────
# Только однозначные executive/founder-роли — это никогда не подходит кандидату.
# director/head/vp/principal/chief переехали в мягкий штраф keywords_penalize
# (config/criteria.yaml) — по TARGET_CRITERIA.md лидерские термины "минус, но не блок".
_EXCLUDED_TITLE_WORDS = {
    "ceo", "cto", "cfo", "coo", "cmo", "founder", "co-founder", "president",
    "трейдер", "trader", "руководитель",
    # Dev-роли — пишем код, не операции
    "solidity", "backend", "frontend", "fullstack", "full-stack",
    "auditor",
    # Финансовые роли без Web3-операций
    "quant", "quantitative",
}

# Dev-роли — слова в ЗАГОЛОВКЕ, которые указывают на разработчика,
# НО допустимы если рядом стоит qa/test/operations/support/community
_DEV_TITLE_WORDS = {"developer", "programmer", "engineer", "devops", "architect"}
_OPS_QA_TITLE_WORDS = {
    "qa", "test", "testing", "tester", "operations", "ops",
    "support", "community", "relations", "automation",
    "rpa", "no-code", "low-code", "workflow",
}

# Роли, для которых проектные хард-гейты (код, домен) не применяются —
# ai_automation сознательно вне крипто-домена и требует лёгкого скриптинга
# (Python/n8n) как часть самой работы, а не как признак SWE-вакансии.
_ROLE_SPECIFIC_GATE_EXEMPT = {"ai_automation"}

# Финансовые роли (только если в заголовке — без web3/crypto контекста)
_FINANCE_TITLE_WORDS = {"portfolio", "fund", "quant", "quantitative"}

# ── 2. Требования писать код (в описании) ─────────────────────────────────────
_CODE_REQUIRED_PATTERN = re.compile(
    r'(?:'
    r'\b(?:selenium|cypress|playwright|pytest|jest|mocha|appium|robot\s+framework)\b'
    r'|\b(?:kubernetes|k8s|helm|terraform|ansible|ci/cd|jenkins|github\s+actions)\s+(?:experience|required|skills?|знани)'
    r'|\b(?:write|develop|code|program|implement|build)\b.{0,40}\b(?:python|javascript|typescript|rust|golang|go|solidity|java|c\+\+)\b'
    r'|\b(?:python|javascript|typescript|rust|golang|solidity)\b.{0,40}\b(?:developer|development|programming|coding|написани)\b'
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

# ── 4. Иностранные языки (кроме ru/en) ────────────────────────────────────────
_FOREIGN_LANG_PATTERN = re.compile(
    r'\b(chinese|mandarin|deutsch|german|french|français|spanish|español|'
    r'japanese|korean|portuguese|arabic|hindi|italian|dutch|turkish|'
    r'польский|немецкий|французский|испанский|китайский|японский|'
    r'корейский|арабский|итальянский|турецкий)\b',
    re.IGNORECASE,
)


def _load_criteria() -> dict:
    return yaml.safe_load(_CRITERIA_FILE.read_text(encoding="utf-8"))


def _load_avoid_keywords() -> set[str]:
    prefs = json.loads((_PROFILE_DIR / "preferences.json").read_text(encoding="utf-8"))
    return {i.lower() for i in prefs.get("industries_avoid", [])}


CRITERIA = _load_criteria()
AVOID_KW = _load_avoid_keywords()
_W = CRITERIA["weights"]


def _matches(term: str, text: str) -> bool:
    """Слово целиком для простых токенов, подстрока — для фраз со спецсимволами."""
    if re.fullmatch(r"[a-zа-я0-9.\- ]+", term) and " " not in term and "." not in term:
        return re.search(r"\b" + re.escape(term) + r"\b", text) is not None
    return term in text


def _hits(terms: list[str], text: str) -> tuple[int, list[str]]:
    found = [t for t in terms if _matches(t, text)]
    return len(found), found


def _n(s: str | None) -> str:
    return (s or "").lower()


def _extra_hard_gates(title: str, text: str, role_key: str) -> str | None:
    """Доп. хард-гейты проекта, дополняющие global_hard_exclude из criteria.yaml.
    Возвращает причину отказа или None если всё ок."""
    title_tokens = set(re.findall(r'[\w-]+', title.lower()))
    blob = f"{_n(title)} {_n(text)}"
    exempt = role_key in _ROLE_SPECIFIC_GATE_EXEMPT

    if any(kw in blob for kw in AVOID_KW):
        return "отраслевой стоп-лист (industries_avoid)"

    if title_tokens & _EXCLUDED_TITLE_WORDS:
        return "executive/dev/finance роль в заголовке"

    if title_tokens & _DEV_TITLE_WORDS and not (title_tokens & _OPS_QA_TITLE_WORDS):
        return "dev-роль без qa/ops/support контекста"

    if title_tokens & _FINANCE_TITLE_WORDS and not (title_tokens & {"web3", "crypto", "blockchain", "defi", "digital"}):
        return "финансовая роль без web3-контекста"

    if not exempt and _CODE_REQUIRED_PATTERN.search(blob):
        return "требуется писать код"

    if _HIGH_EXP_PATTERN.search(blob):
        return "требуется 7+ лет опыта"

    if _FOREIGN_LANG_PATTERN.search(blob):
        return "требуется язык кроме ru/en"

    return None


def passes_hard_gates(title: str, text: str, role_key: str) -> tuple[bool, list[str]]:
    """Дешёвый предфильтр. Возвращает (bool, [причины])."""
    role = CRITERIA["roles"][role_key]
    blob = f"{_n(title)} {_n(text)}"

    for t in CRITERIA["global_hard_exclude"]:
        if _matches(t, blob):
            return False, [f"hard-exclude: '{t}'"]

    extra_reason = _extra_hard_gates(title, text, role_key)
    if extra_reason:
        return False, [extra_reason]

    n_role, _ = _hits(role["must_role"], blob)
    if n_role == 0:
        return False, ["нет ролевых ключевых слов (не та функция)"]

    if role.get("must_domain"):
        n_dom, _ = _hits(role["must_domain"], blob)
        if n_dom == 0:
            return False, ["нет крипто/web3 контекста (не та область)"]

    return True, ["gate ok"]


def score_vacancy(title: str, text: str, role_key: str) -> dict:
    """Балл 0..100 + рекомендация + причины. Сначала прогоняет гейты."""
    role = CRITERIA["roles"][role_key]
    ok, reasons = passes_hard_gates(title, text, role_key)
    if not ok:
        return {"role": role_key, "passed_gate": False, "score": 0,
                "recommend": False, "reasons": reasons}

    blob = f"{_n(title)} {_n(text)}"
    score = _W["gate_base"]
    reasons = []

    if _hits(role["titles_strong"], blob)[0]:
        score += _W["title_strong"]; reasons.append("сильное совпадение по должности")
    elif _hits(role["titles_weak"], blob)[0]:
        score += _W["title_weak"]; reasons.append("смежная должность")
    else:
        reasons.append("должность напрямую не совпала")

    nb, fb = _hits(role["keywords_boost"], blob)
    if nb:
        add = min(nb * _W["boost_each"], _W["boost_cap"])
        score += add; reasons.append(f"+{add} релевантные навыки: {', '.join(fb[:6])}")

    npz, fpz = _hits(role["keywords_penalize"], blob)
    if npz:
        sub = max(npz * _W["penalize_each"], _W["penalize_cap"])
        score += sub; reasons.append(f"{sub} нерелевантные/несоответствующие: {', '.join(fpz[:6])}")

    # Лидерские термины штрафуют только в заголовке: «you will lead» в описании
    # — не лидерская роль (калибровка, docs/PREFILTER_AUDIT.md §5.2)
    npt, fpt = _hits(role.get("keywords_penalize_title", []), _n(title))
    if npt:
        sub = max(npt * _W["penalize_each"], _W["penalize_cap"])
        score += sub; reasons.append(f"{sub} лидерская роль в заголовке: {', '.join(fpt[:4])}")

    if _hits(role["senior_terms"], _n(title))[0]:
        score += _W["senior"]; reasons.append("senior-уровень (мягкий штраф)")

    reloc = _hits(CRITERIA["relocation_ok"], blob)[0]
    remote = _hits(CRITERIA["remote_boost"], blob)[0]
    onsite = _hits(CRITERIA["onsite_penalty"], blob)[0]
    if remote:
        score += _W["remote"]; reasons.append("remote")
    if reloc:
        score += _W["relocation"]; reasons.append("страна релокации подходит")
    if onsite and not remote and not reloc:
        score += _W["onsite"]; reasons.append("только офис в неподходящей локации")

    ew = role["english_weight"]
    nep, fep = _hits(CRITERIA["english_penalty"], blob)
    if nep:
        sub = int(nep * _W["english_penalty_each"] * ew)
        score += sub; reasons.append(f"{sub} требуется сильный английский: {', '.join(fep[:4])}")
    if _hits(CRITERIA["english_boost"], blob)[0]:
        add = int(_W["english_boost"] * ew)
        score += add; reasons.append(f"+{add} русскоязычная/CIS команда")

    score = max(0, min(100, score))
    return {
        "role": role_key,
        "passed_gate": True,
        "score": score,
        "recommend": score >= role["threshold"],
        "reasons": reasons,
    }


def score_job(job: Job) -> dict:
    """Скорит вакансию по обеим ролям (crypto_ops, web3_support), возвращает лучшую."""
    results = [score_vacancy(job.title, job.description, r) for r in CRITERIA["roles"]]
    best = max(results, key=lambda r: r["score"])
    return {"best": best, "all": results}


def passes_pre_filter(job: Job) -> bool:
    """Обёртка для scheduler.py — true если вакансия прошла гейт и достигла порога."""
    best = score_job(job)["best"]
    return best["passed_gate"] and best["recommend"]
