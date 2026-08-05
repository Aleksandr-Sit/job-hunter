import hashlib
import json
import re
from functools import lru_cache
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
# НО допустимы если рядом стоит qa/test/operations/support/community.
# RU: «инженер» НЕ включаем — перегружен («инженер поддержки» должен проходить);
# берём только однозначно-девелоперские существительные.
_DEV_TITLE_WORDS = {"developer", "programmer", "engineer", "devops", "architect",
                    "разработчик", "программист"}
_OPS_QA_TITLE_WORDS = {
    "qa", "test", "testing", "tester", "operations", "ops",
    "support", "community", "relations", "automation",
    "rpa", "no-code", "low-code", "workflow",
    # RU-аналоги (частые формы) — страхуют, если dev-слово рядом с ops/qa-ролью
    "тестировщик", "поддержка", "поддержки", "оператор", "сообщества",
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
# Голое «N years» — НЕ требование опыта: описания компаний часто содержат
# «has spent the last 15 years building…» (возраст компании), из-за чего гейт
# молча резал релевантные ops-роли (HEALTH_AUDIT F10, замер 25.07). Поэтому
# «N лет» считаем требованием ТОЛЬКО (а) при явной нижней границе
# («from/over/at least/от/не менее 7 лет») или (б) рядом с контекстом опыта
# и НЕ рядом с историей компании.
_YEARS_NUM_PATTERN = re.compile(
    r'\b(?:[7-9]|1\d|20)\+?\s*(?:years?|лет|года?|г\.)',
    re.IGNORECASE,
)
_EXPLICIT_HIGH_EXP = re.compile(
    r'\b(?:more\s+than|более\s+чем|свыше|at\s+least|не\s+менее|from|от|min(?:imum)?\.?)\s*'
    r'(?:[7-9]|1\d|20)\+?\s*(?:years?|лет)',
    re.IGNORECASE,
)
_EXP_REQ_CONTEXT = re.compile(
    r'(experience|exp\.|опыт|стаж|require|minimum|min\.|at\s+least|'
    r'не\s+менее|professional|track\s+record|proven|in\s+a\s+similar)',
    re.IGNORECASE,
)
_EXP_HISTORY_CONTEXT = re.compile(
    r'(spent|build|built|founded|history|for\s+the\s+(?:last|past)|'
    r'over\s+the\s+(?:last|past)|за\s+последн|основан|we\s+have\s+been|our\s+journey)',
    re.IGNORECASE,
)


def _high_exp_required(blob: str) -> bool:
    """True, если требуется 7+ лет опыта (а не просто упомянуто «N лет»)."""
    if _EXPLICIT_HIGH_EXP.search(blob):
        return True
    for m in _YEARS_NUM_PATTERN.finditer(blob):
        window = blob[max(0, m.start() - 40): m.end() + 40]
        if _EXP_REQ_CONTEXT.search(window) and not _EXP_HISTORY_CONTEXT.search(window):
            return True
    return False

# ── 3b. Требование гражданства / права на работу ──────────────────────────────
# Кандидат — гражданин РФ с разрешением на работу только в РФ. Вакансии,
# требующие паспорт/гражданство ЕС или США, недостижимы независимо от совпадения
# по навыкам, и спонсорство их не решает (паспорт не спонсируют). Найдено 02.08
# на реальной вакансии: удалённая Russian-speaking роль с «valid EU passport»
# набирала 84 балла и была бы отправлена.
_CITIZENSHIP_REQUIRED = re.compile(
    r'\b(eu passport|european passport|eu citizenship|eu citizen|'
    r'us citizen|green card|uk citizen|british passport|'
    r'гражданство ес|паспорт ес|гражданство сша)\b',
    re.IGNORECASE,
)
# «right/eligible/authorized to work in X» — дисквалифицирует, только если X не РФ
# (иначе отсекли бы обычные российские вакансии «право работать в РФ»).
_RIGHT_TO_WORK = re.compile(
    r'(?:right to work|eligible to work|authoriz(?:ed|ation) to work|legally able to work)'
    r'\s+in\s+(?:the\s+)?([a-zа-я\s]{2,20})',
    re.IGNORECASE,
)
_NON_RF_JURISDICTION = re.compile(
    r'\b(eu|e\.u\.|europe|european union|uk|united kingdom|us|u\.s\.|usa|'
    r'united states|schengen|germany|poland|bulgaria|portugal|spain|cyprus|'
    r'netherlands|ireland|france|italy|greece)\b',
    re.IGNORECASE,
)


def _citizenship_barrier(blob: str) -> bool:
    """True, если вакансия требует гражданство/право на работу, которых нет."""
    if _CITIZENSHIP_REQUIRED.search(blob):
        return True
    for m in _RIGHT_TO_WORK.finditer(blob):
        if _NON_RF_JURISDICTION.search(m.group(1)):
            return True
    return False


# ── 4. Иностранные языки (кроме ru/en) ────────────────────────────────────────
_FOREIGN_LANG_PATTERN = re.compile(
    r'\b(chinese|mandarin|deutsch|german|french|français|spanish|español|'
    r'japanese|korean|portuguese|arabic|hindi|italian|dutch|turkish|'
    r'польский|немецкий|французский|испанский|китайский|японский|'
    r'корейский|арабский|итальянский|турецкий)\b',
    re.IGNORECASE,
)

# «Spanish is a plus» — не требование: не блокируем, если рядом сигнал желательности
_LANG_PLUS_PATTERN = re.compile(
    r'(is\s+a\s+plus|as\s+a\s+plus|nice[\s-]to[\s-]have|would\s+be\s+a\s+plus|'
    r'advantage|beneficial|preferred|bonus|плюсом|преимуществ|приветствуется|желательно)',
    re.IGNORECASE,
)


# Заголовки разделов. Язык, упомянутый в разделе «желательно», НЕ является
# требованием — окна ±60 символов для этого мало: заголовок раздела стоит выше,
# через несколько пунктов списка. Реальный случай 05.08.2026: Fireblocks
# «Technical Support Engineer, APAC» — Mandarin на позиции 2901, а заголовок
# «Preferred qualifications» на 2639 (за 262 символа) → гейт считал язык
# обязательным и резал подходящую вакансию.
_OPTIONAL_SECTION = re.compile(
    r'(preferred qualification|preferred skill|nice[\s-]to[\s-]have|'
    r'bonus point|good to have|desirable|advantageous|will be a plus|'
    r'будет плюсом|желательн|приветствуется|как преимущество)',
    re.IGNORECASE,
)
_REQUIRED_SECTION = re.compile(
    r'(requirements|required qualification|must have|minimum qualification|'
    r'what you.{0,3}ll need|essential|обязательные требования|требования:)',
    re.IGNORECASE,
)


def _in_optional_section(blob: str, pos: int) -> bool:
    """True, если позиция находится в разделе «желательно», а не «требования».
    Сравниваем, какой заголовок ближе слева."""
    head = blob[:pos]
    last_opt = max((m.start() for m in _OPTIONAL_SECTION.finditer(head)), default=-1)
    last_req = max((m.start() for m in _REQUIRED_SECTION.finditer(head)), default=-1)
    return last_opt > last_req


def _foreign_lang_required(blob: str) -> bool:
    """True, если иностранный язык требуется (а не «будет плюсом»).

    Два уровня проверки: (1) пометка «плюс» рядом с самим языком;
    (2) язык внутри раздела «Preferred/Nice to have» — тоже не требование.
    """
    for m in _FOREIGN_LANG_PATTERN.finditer(blob):
        window = blob[max(0, m.start() - 60): m.end() + 60]
        if _LANG_PLUS_PATTERN.search(window):
            continue
        if _in_optional_section(blob, m.start()):
            continue
        return True  # упоминание без пометки «плюс» и вне «желательного» раздела
    return False


def _load_criteria() -> dict:
    return yaml.safe_load(_CRITERIA_FILE.read_text(encoding="utf-8"))


def _load_avoid_keywords() -> set[str]:
    """Отраслевой стоп-лист из профиля.

    Профиль личный и в .gitignore (см. CLAUDE.md), поэтому в свежем клоне и в CI
    его нет — читаем `.example`, а если и его нет, работаем с пустым стоп-листом.
    Импорт модуля не должен падать из-за отсутствия личного файла.
    """
    for name in ("preferences.json", "preferences.json.example"):
        path = _PROFILE_DIR / name
        try:
            prefs = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        return {i.lower() for i in prefs.get("industries_avoid", [])}
    return set()


CRITERIA = _load_criteria()
AVOID_KW = _load_avoid_keywords()
_W = CRITERIA["weights"]


@lru_cache(maxsize=1)
def _prefilter_version() -> str:
    """Отпечаток критериев + логики фильтра. Меняется — pre-filter-отказы в
    seen_jobs считаются устаревшими и переоцениваются (зеркало _scoring_version
    для AI-кэша). Хешируем criteria.yaml (главный рычаг) и собственный исходник
    (правки логики гейтов деплоятся вместе с кодом)."""
    try:
        criteria = _CRITERIA_FILE.read_text(encoding="utf-8")
    except OSError:
        criteria = ""
    source = Path(__file__).read_text(encoding="utf-8")
    blob = criteria + source
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:12]


def _matches(term: str, text: str) -> bool:
    """Матч термина в тексте. Три режима:
    - `стем*` — префиксный матч от границы слова (`\\bстем`): ловит все падежные
      формы («операц*» → операции/операциям/операционный), но не середину слова
      («кооперация» не сматчит «операц*»). Нужен для русской морфологии.
    - одиночный токен без спецсимволов — слово целиком (`\\bслово\\b`).
    - фраза/со спецсимволами — подстрока.
    """
    if term.endswith("*"):
        stem = term[:-1]
        return re.search(r"\b" + re.escape(stem), text) is not None
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

    if _high_exp_required(blob):
        return "требуется 7+ лет опыта"

    if _foreign_lang_required(blob):
        return "требуется язык кроме ru/en"

    if _citizenship_barrier(blob):
        return "требуется гражданство/право на работу в ЕС или США"

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

    title_is_strong = _hits(role["titles_strong"], blob)[0]
    if title_is_strong:
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

    # Senior-штраф не рубит прицельный ops-тайтл: «Senior Operations Specialist» —
    # это профильная роль, а не «слишком старшая». gate_base+title_strong=50, штраф
    # −6 ронял такие на 44 < порога (Ripple/Coinbase/Soberin ops, DIAGNOSE 08.07).
    if not title_is_strong and _hits(role["senior_terms"], _n(title))[0]:
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

    # Entry/junior/обучающие роли — целевой сегмент кандидата (низкий барьер)
    if _hits(CRITERIA.get("entry_boost", []), blob)[0]:
        score += _W["entry"]; reasons.append(f"+{_W['entry']} entry/junior (низкий барьер)")

    score = max(0, min(100, score))
    return {
        "role": role_key,
        "passed_gate": True,
        "score": score,
        "recommend": score >= role["threshold"],
        "reasons": reasons,
    }


def dedupe_key(company: str | None, title: str | None) -> str:
    """Ключ near-дубликата: одна и та же роль на разных бордах (или в нескольких
    локациях) приходит с РАЗНЫМИ id и проходит дедуп по id. В боевом прогоне
    25.07 так пришли «Professional Services Consultant @ Ripple» ×3 и
    «Risk & Monitoring Analyst IV @ Coinbase» ×2 (HEALTH_AUDIT M2).

    Нормализуем: регистр, пунктуация, лишние пробелы и хвосты локаций/уровней
    в скобках («… (EMEA)», «… (Remote)») не должны делать вакансии разными.
    """
    text = f"{_n(company)}|{_n(title)}"
    text = re.sub(r"\([^)]*\)", " ", text)          # хвосты в скобках
    text = re.sub(r"[^\w|]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def dedupe_jobs(pairs: list) -> list:
    """Оставляет по одному представителю на near-дубликат, сохраняя порядок.
    Вход: [(Job, MatchResult)] — как в scheduler перед отправкой."""
    seen: set[str] = set()
    out = []
    for item in pairs:
        job = item[0] if isinstance(item, tuple) else item
        key = dedupe_key(getattr(job, "company", ""), getattr(job, "title", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def score_job(job: Job) -> dict:
    """Скорит вакансию по всем ролям из criteria.yaml (crypto_ops, web3_support,
    ai_automation, qa_web3), возвращает лучшую по баллу."""
    results = [score_vacancy(job.title, job.description, r) for r in CRITERIA["roles"]]
    best = max(results, key=lambda r: r["score"])
    return {"best": best, "all": results}


def passes_pre_filter(job: Job) -> bool:
    """Обёртка для scheduler.py — true если вакансия прошла гейт и достигла порога."""
    best = score_job(job)["best"]
    return best["passed_gate"] and best["recommend"]
