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


# РФ/СНГ-локации: работать там кандидат может без визы и переезда, поэтому
# указание такого города НЕ считается «офисом в неподходящей локации».
_RU_LOCATION = re.compile(
    r'(росси|russia|москв|moscow|санкт|петербург|spb|самар|samara|'
    r'екатеринбург|новосибирск|казан|нижний новгород|краснодар|'
    r'снг|\bcis\b|беларус|belarus|казахстан|kazakhstan|армени|armenia|'
    r'удалён|удален|remote|anywhere)',
    re.IGNORECASE,
)

# ЯВНОЕ заявление удалённого формата — в отличие от одиночного слова «remote»,
# которое встречается в «remote troubleshooting», «remote access», «remote server».
_EXPLICIT_REMOTE = re.compile(
    r'(fully[\s-]remote|100%\s*remote|remote[\s-]first|work from anywhere|'
    r'remote position|remote role|fully distributed|'
    r'полностью удалённ|полностью удаленн|удалённая работа|удаленная работа|'
    r'удалённый формат|удаленный формат)',
    re.IGNORECASE,
)

# ── 3c. Требование СВОБОДНОГО английского ─────────────────────────────────────
# У кандидата A1–A2. «Fluent/native English» в требованиях — такой же
# недостижимый барьер, как паспорт ЕС: резюме и сопроводительное его не решают.
# Раньше это был лишь мягкий штраф −14, который перебивался сильным заголовком
# (замер 05.08.2026: удалённая крипто-вакансия с «Fluent in English» = 64 балла
# и уходила кандидату). Цена гейта измерена: из 233 проходящих вакансий его
# требуют 4 (2%), и ни одна из них не русскоязычная.
_FLUENT_ENGLISH_REQUIRED = re.compile(
    r'('
    # «Fluent in RUSSIAN AND English» (Epson, 14.08.2026) — между «in» и «English»
    # может стоять перечисление других языков.
    # «Fluent WRITTEN AND SPOKEN English» (RONIN Europe) — слова между «fluent»
    # и «English» бывают не только после «in».
    r'fluent\s+(?:\w+\s+){0,4}english|native\s+english|'
    r'native[\s-]level\s+english|'
    # Уровни CEFR: у кандидата A1–A2, поэтому барьер — начиная с B1
    # («English B1+ (correspondence, calls…)», Opiniq, 14.08.2026).
    r'english\s+(?:at\s+)?(?:level\s+)?(?:b1|b2|c1|c2)\+?\b|'
    r'\b(?:b1|b2|c1|c2)\+?\s+(?:level\s+)?english\b|'
    r'\benglish\s+(?:fluency|proficiency)|'
    # «Fluency in BOTH RUSSIAN AND English» (Opiniq) — между «in» и «English»
    # бывает перечисление других языков.
    r'fluency\s+in\s+(?:\w+\s+){0,3}english|proficien\w*\s+in\s+english|'
    # «Good communication skills (both verbal and written, Russian and English)»
    # — язык перечислен в скобках, предлога «in English» нет (Opiniq).
    r'communicat\w*\s+skills?\s*\([^)]{0,90}english|'
    # «Excellent COMMAND OF spoken and written English», «strong knowledge of
    # business English» — между качеством и словом English бывает до 4 слов
    # (найдено 06.08.2026 на вакансии OKX: шаблон без этого не срабатывал).
    r'(?:excellent|strong|advanced|high|professional|full|solid|great|good)\s+'
    # «Good SKILLS in English language» (Iron Mountain, 14.08.2026) — существительное
    # не только command/level, но и skills.
    r'(?:command|proficiency|knowledge|level|standard|skills?)\s+(?:of|in)\s+'
    r'(?:\w+\s+){0,4}english|'
    r'(?:excellent|strong|advanced|professional)\s+(?:\w+\s+){0,3}english\b|'
    # «Excellent written and verbal communication skills IN ENGLISH» — между
    # качеством и словом English шесть слов, и существительное здесь не
    # «command/level», а «communication skills» (найдено 14.08.2026 на вакансии
    # Crypto Casino Operations Manager, SearchTalent).
    r'(?:excellent|strong|advanced|high|professional|solid|great|good|effective)\s+'
    r'(?:\w+\s+){0,5}communicat\w*(?:\s+skills?)?\s+(?:in|of)\s+english|'
    r'communicat\w*\s+(?:effectively\s+)?in\s+english\b|'
    # «You are COMFORTABLE IN ENGLISH, spoken and written» (Vision Compliance,
    # 14.08.2026) — требование через самочувствие, а не через уровень.
    r'(?:comfortable|confident|at\s+ease)\s+(?:\w+\s+){0,2}in\s+english\b|'
    r'свободный\s+английск|уровень\s+носителя|'
    # Уровень после слова «английский» пишут по-разному: «английский C1»,
    # «английский — B2», «английский язык: C1», «английский уровня B2», «английский
    # от B2». Раньше ловились только два точных написания с латинской буквой.
    r'английск\w*(?:\s+язык\w*)?\s*[-—:]?\s*(?:уровн\w*\s+|от\s+|не\s+ниже\s+)?[bc][12]\+?|'
    r'свободное\s+владение\s+английск|отличное\s+владение\s+английск'
    r')',
    re.IGNORECASE,
)


# Модальность требования. Разговорный английский кандидату недоступен (A1–A2),
# а письменный закрывается переводчиком/AI — это разные барьеры, и мешать их в один
# гейт неверно (решение владельца 05.08.2026).
_SPOKEN_MARKERS = re.compile(
    # Суффиксы обязательны: «verbally and in writing» (Binance) при `verbal\b`
    # определялось как ПИСЬМЕННОЕ требование — найдено замером 05.08.2026.
    r'\b(spoken|verbal\w*|oral\w*|conversation\w*|speak\w*|phone|calls?|'
    r'meetings?|video|negotiat\w*|presentation\w*|stand-?ups?)\b|'
    r'устн\w*|разговорн\w*|переговор\w*|созвон\w*|звонк\w*',
    re.IGNORECASE,
)
# ЯВНЫЙ признак русскоязычного контура: роль обслуживает русскоговорящих. Отличать
# от простого упоминания русского среди требуемых языков — иначе «Fluent in Russian
# and English» в международной компании (Epson) считается RU-desk и гейт молчит.
# Наш язык — продолжение перечисления, начатого сразу после него.
# Только союз, НЕ запятая: «English, Georgian languages are preferred» (Epson) —
# запятая начинает новую мысль о другом языке, а «Russian AND/OR Greek» (RONIN)
# — одно перечисление, и пометка «плюс» относится ко всему списку.
_LIST_CONTINUATION = re.compile(r'^\s*(?:and\s*/?\s*or|and|or|или|и)\s+',
                                re.IGNORECASE)

_RU_MENTION = re.compile(r'\brussian\w*|русск\w*|\bcis\b|\bснг\b', re.IGNORECASE)

_RU_DESK_STRONG = re.compile(
    r'russian[\s-]speak\w*|russian\s+speakers?|\bcis\b|russian\s+desk|'
    r'russian[\s-]language\s+(?:support|desk|team)|'
    r'русскоязычн\w*|русскоговорящ\w*|\bснг\b',
    re.IGNORECASE,
)

# Английский и русский требуются вместе, одной фразой — это НЕ русскоязычный контур.
_EN_AND_RU = re.compile(
    r'(english\s*(?:,|and|&|/|или|и)\s*(?:\w+\s+){0,2}russian|'
    r'russian\s*(?:,|and|&|/|или|и)\s*(?:\w+\s+){0,2}english|'
    r'англ\w*\s*(?:,|и|/)\s*русск|русск\w*\s*(?:,|и|/)\s*англ)',
    re.IGNORECASE,
)

# Уровень назван числом: B1/B2 — это «дотягиваемо и стоит посмотреть», в отличие
# от размытого «fluent». C1/C2 остаются жёстким барьером (решение владельца
# 14.08.2026: «переведи B1-B2 в штраф, интересно посмотреть, какие приходят с
# очень высокой оценкой»).
_CEFR_B_LEVEL = re.compile(r'\bb[12]\+?\b', re.IGNORECASE)
_CEFR_C_LEVEL = re.compile(r'\bc[12]\+?\b', re.IGNORECASE)


# Граница ПУНКТА — уже, чем _SENTENCE_BREAK: точка с запятой обычно разделяет части
# одного требования («English at C1 level; daily calls with partners»), а не разные
# требования, и резать по ней — потерять модальность, которая рядом. Отдельные
# пункты разделяются точкой, буллетом или переводом строки.
_REQUIREMENT_BREAK = re.compile(r"[.•·\n]")


def _requirement_window(blob: str, start: int, end: int, span: int = 90) -> str:
    """Окно вокруг требования, обрезанное по границе пункта.

    Модальность нельзя брать из СОСЕДНЕГО требования. Найдено 17.08.2026 на
    вакансии Synergy of Lake Technology: в окно попадало «отличные навыки ведения
    переговоров» — отдельный пункт двумя строками выше, — и «Английский С1»
    получал модальность spoken. Хотя поддержка в этой вакансии прямым текстом
    описана как текстовая (email, чаты, соцсети), а про устный английский там
    не сказано ничего.
    """
    head = blob[max(0, start - span): start]
    tail = blob[end: end + span]
    breaks = list(_REQUIREMENT_BREAK.finditer(head))
    if breaks:
        head = head[breaks[-1].end():]
    nxt = _REQUIREMENT_BREAK.search(tail)
    if nxt:
        tail = tail[:nxt.start()]
    return head + blob[start:end] + tail

_WRITTEN_MARKERS = re.compile(
    r'\b(written|writing|correspondence|documentation)\b|письмен\w*|переписк\w*',
    re.IGNORECASE,
)

def _english_modality(blob: str) -> str | None:
    """Насколько жёстко вакансия требует английский: spoken / generic / written / None.

    Исключения (требования нет): (1) пометка «плюс» рядом; (2) требование в разделе
    «желательно»; (3) вакансия русскоязычная/CIS — там английский обычно вторичен,
    и решение об отклике кандидат принимает сам.
    """
    # Упоминание русского обычно означает русскоязычную команду — тогда английский
    # вторичен и решение об отклике за кандидатом. НО если английский и русский
    # требуются В ОДНОЙ фразе («Fluent in Russian and English»), это не
    # русскоязычный контур, а требование обоих языков — исключение не применяем
    # (найдено 14.08.2026 на вакансиях COLIBRIX ONE и Epson Middle East).
    if _hits(CRITERIA["english_boost"], blob)[0]:
        # Русский, сам помеченный как ПЛЮС, признаком русскоязычного контура не
        # является. «Fluent written and spoken English; knowledge of Russian
        # and/or Greek would be considered an advantage» (RONIN Europe,
        # 14.08.2026) — здесь обязателен английский, а русский лишь желателен.
        ru = [m for m in _RU_MENTION.finditer(blob)]
        ru_only_optional = bool(ru) and all(
            _mention_is_optional(blob, m.start(), m.end()) for m in ru)
        # Явный RU-desk («russian speaking», «CIS», «русскоязычные клиенты») —
        # исключение работает всегда. Иначе оно снимается, если английский и
        # русский требуются ОДНОЙ фразой: это не русскоязычный контур, а
        # требование обоих языков (COLIBRIX ONE, Epson — 14.08.2026).
        if not ru_only_optional and (
                _RU_DESK_STRONG.search(blob) or not _EN_AND_RU.search(blob)):
            return None
    found: set[str] = set()
    for m in _FLUENT_ENGLISH_REQUIRED.finditer(blob):
        if _mention_is_optional(blob, m.start(), m.end()):
            continue
        if _in_optional_section(blob, m.start()):
            continue
        # Модальность ищем в окне вокруг самого требования, обрезанном по границе
        # пункта: соседнее требование про английский ничего не говорит.
        window = _requirement_window(blob, m.start(), m.end())
        # Уровень ищем и в хвосте: «fluent russian and english» съедает слово
        # English, и отдельный шаблон «english level b2» уже не срабатывает —
        # finditer не даёт пересекающихся совпадений.
        tail30 = m.group(0) + " " + blob[m.end(): m.end() + 30]
        if _CEFR_B_LEVEL.search(tail30):
            # Работодатель назвал уровень числом — верим ему, а не окружающим
            # словам. Если в вакансии есть ОТДЕЛЬНОЕ требование устного
            # английского, оно даст свой матч и победит по строгости ниже.
            found.add("level_b")
        elif _SPOKEN_MARKERS.search(window):
            found.add("spoken")
        elif _CEFR_C_LEVEL.search(tail30):
            # C1/C2 БЕЗ пометки про устный. Решение владельца 17.08.2026: жёстко
            # не резать. Разговорный английский закрыт, а письменный C1 частично
            # закрывается переводчиком и AI — такие вакансии не приоритет, но
            # рассматривать их можно. Поэтому штраф, а не отсев.
            # Если устный английский заявлен явно, ветка выше уже дала "spoken".
            found.add("level_c")
        elif _WRITTEN_MARKERS.search(window):
            found.add("written")
        else:
            found.add("generic")
    # Названный числом уровень важнее размытого прилагательного рядом:
    # «Fluent Russian and English level B2» — верим «B2», а не «fluent».
    # Но явное требование УСТНОГО английского перебивает и его.
    # Порядок = «кто побеждает, если в вакансии несколько упоминаний».
    # Устный перебивает всё. Дальше — названный числом уровень важнее размытого
    # прилагательного (C строже B), и только потом безликое «fluent English».
    for level in ("spoken", "level_c", "level_b", "generic", "written"):
        if level in found:
            return level
    return None


def _fluent_english_required(blob: str) -> bool:
    """True, если требование английского — жёсткий барьер (см. _ENGLISH_GATE_LEVELS)."""
    return _english_modality(blob) in _ENGLISH_GATE_LEVELS


# ── 3b. Ночные смены и сменный график ─────────────────────────────────────────
# Решение владельца 19.08.2026: ночные смены как РЕЖИМ работы — жёсткий отсев,
# редкие ночи и сменный график — штраф. Поводом стали две вакансии, приехавшие
# 19.08: «Специалист поддержки» Контура («вечерние или ночные смены») и
# «PSP Support Agent» SOFTSWISS («2/2 shift schedule, 2–4 night shifts per month»).

# Считанные ночи в месяц — это не режим, а исключение. Проверяется ПЕРВЫМ,
# иначе «2–4 night shifts per month» попадёт под жёсткий отсев ниже.
_NIGHT_OCCASIONAL = re.compile(
    r'\d{1,2}\s*[-–—]?\s*\d{0,2}\s*night\s+shifts?\s+(?:per|a|/)\s*month|'
    r'\d{1,2}\s*[-–—]?\s*\d{0,2}\s*ночн\w*\s+смен\w*\s+в\s+месяц|'
    r'occasional\w*\s+night|'
    r'иногда\s+ночн|редк\w*\s+ночн',
    re.IGNORECASE,
)

# Ночь как штатный режим.
_NIGHT_CORE = re.compile(
    r'ночн\w*\s+смен|ночн\w*\s+график|в\s+ночную\s+смену|'
    r'работа\s+в\s+ночн\w*|ночн\w*\s+врем\w*\s+сут|'
    r'night\s+shift|graveyard\s+shift|overnight\s+shift|'
    r'сутки\s+через',          # суточный график ночь включает по определению
    re.IGNORECASE,
)

# Сменный график без ночей. «5/2» СЮДА НЕ ПОПАДАЕТ намеренно — это обычная
# пятидневка, а не сменная работа; штрафовать её значило бы срезать половину
# нормальных вакансий. Числовое отношение считается только рядом со словом
# про график, иначе regex ловит дроби и даты.
_SHIFT_SCHEDULE = re.compile(
    r'сменн\w*\s+график|график\w*\s+сменн|сменн\w*\s+работ|посменн|'
    r'shift\s+schedule|rotating\s+shifts?|rotational\s+shift|shift\s+work|'
    r'(?:график|режим|schedule|shift)\D{0,20}\b[1-4]\s*/\s*[1-4]\b|'
    r'\b[1-4]\s*/\s*[1-4]\b\D{0,20}(?:смен|shift|график)',
    re.IGNORECASE,
)


def _night_shift_mode(blob: str) -> str | None:
    """Как в вакансии устроены смены: 'core' | 'occasional' | 'shift' | None.

    'core'       — ночь штатный режим -> жёсткий отсев;
    'occasional' — считанные ночи в месяц -> штраф;
    'shift'      — сменный график без ночей -> меньший штраф.
    """
    if _NIGHT_OCCASIONAL.search(blob):
        return "occasional"
    if _NIGHT_CORE.search(blob):
        return "core"
    if _SHIFT_SCHEDULE.search(blob):
        return "shift"
    return None


# ── 4. Иностранные языки (кроме ru/en) ────────────────────────────────────────
_FOREIGN_LANG_PATTERN = re.compile(
    r'\b(chinese|mandarin|cantonese|deutsch|german|french|français|spanish|español|'
    r'japanese|korean|portuguese|arabic|hindi|italian|dutch|turkish|'
    # Языки стран релокации: без них поиск по Греции/Вьетнаму/Сербии тащит
    # вакансии с обязательным местным языком (замер 06.08.2026 — «Fluent Greek
    # language is required» и «Fluent Vietnamese required» проходили гейт).
    r'greek|vietnamese|serbian|croatian|indonesian|bahasa|thai|kazakh|'
    r'georgian|armenian|uzbek|azerbaijani|hebrew|romanian|bulgarian|'
    # Скандинавские: замер 06.08.2026 пропустил «Danish FinTech Advisor».
    # «polish» намеренно НЕ добавлен — в английском это ещё и «шлифовать»
    # («polish your skills»), ловил бы ложно.
    r'danish|swedish|norwegian|finnish|czech|hungarian|'
    # Украинский и белорусский: их не было вообще, хотя соседние языки СНГ
    # (казахский, грузинский, армянский, узбекский) в списке стояли. Замер
    # 21.08.2026 — «Account Manager» у Statok, Ларнака: «High proficiency in
    # spoken Ukrainian is a must», балл 66, вакансия ушла в Telegram.
    r'ukrainian|belarusian|'
    # Русские названия — СТЕМАМИ, а не именительным падежом. Раньше стояло
    # одиночное «польский», и «знание польскОГО языка» не ловилось: слово
    # целиком не совпадало. Список заодно выровнен с английской половиной —
    # в ней были иврит, чешский и венгерский, которых в русской не хватало.
    r'польск\w*|немецк\w*|французск\w*|испанск\w*|китайск\w*|японск\w*|'
    r'корейск\w*|арабск\w*|итальянск\w*|турецк\w*|греческ\w*|вьетнамск\w*|'
    r'сербск\w*|индонезийск\w*|тайск\w*|казахск\w*|грузинск\w*|армянск\w*|'
    r'узбекск\w*|украинск\w*|белорусск\w*|азербайджанск\w*|иврит\w*|'
    r'румынск\w*|болгарск\w*|датск\w*|шведск\w*|норвежск\w*|финск\w*|'
    r'чешск\w*|венгерск\w*|голландск\w*|нидерландск\w*)\b',
    re.IGNORECASE,
)

# «Spanish is a plus» — не требование: не блокируем, если рядом сигнал желательности
_LANG_PLUS_PATTERN = re.compile(
    # «ARE a plus», «WILL BE a plus» — были не покрыты, ловилось только «is a plus»
    # (найдено 14.08.2026 при разборе Teroxx).
    r'((?:is|are|would\s+be|will\s+be|as)\s+a\s+plus|nice[\s-]to[\s-]have|'
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


_SENTENCE_BREAK = re.compile(r"[.;•·\n]")

# Заголовком раздела может быть только пометка соответствующей ФОРМЫ. Одиночные
# «advantage», «preferred», «beneficial», «bonus» — всегда часть предложения
# («…would be considered an advantage», RONIN; «…strongly preferred», Daman).
_HEADERISH_OPTIONAL = re.compile(
    r'^(?:nice[\s-]to[\s-]have|preferred\s+qualification\w*|preferred\s+skill\w*|'
    r'bonus\s+point\w*|good\s+to\s+have|desirable|advantageous|желательн\w*)$',
    re.IGNORECASE,
)
# «… is nice to have» — глагол-связка перед пометкой означает, что это оговорка
# к текущему пункту, а не заголовок следующего раздела.
# Артикль между связкой и пометкой: «English is A nice-to-have» читалось как
# ЗАГОЛОВОК раздела, потому что «a» разрывала связку и хвост не совпадал с
# `\s*$`. Пометка «плюс» отбрасывалась, и вакансия с ЖЕЛАТЕЛЬНЫМ языком
# уходила в жёсткий отсев. Найдено 21.08.2026 на «Ukrainian language courses
# are a nice-to-have benefit»; тот же баг задевал и гейт английского.
_LINKING_VERB_BEFORE = re.compile(
    r'\b(is|are|was|were|be|been|will|would|it.s|это)\s*(?:an?|the)?\s*$',
    re.IGNORECASE)


def _marker_is_header(before: str, marker: str) -> bool:
    """Пометка «желательно» — ЗАГОЛОВОК раздела, а не оговорка к текущему пункту?

    Различать обязательно, иначе оговорка гасит требования соседних пунктов, а
    заголовок наоборот делает обязательным то, что помечено как плюс.
    Границей предложения пользоваться НЕЛЬЗЯ: после снятия HTML списки часто идут
    без точек, и настоящий заголовок выглядит стоящим в середине фразы (Yodeck:
    «…at C1 level or above Nice to have Previous helpdesk…»).
    """
    if not _HEADERISH_OPTIONAL.match(marker.strip()):
        return False
    return not _LINKING_VERB_BEFORE.search(before[-16:])

# Обобщённое упоминание других языков — «additional languages are an advantage».
_OTHER_LANG_GENERIC = re.compile(
    r'(additional|other|second|further|extra|foreign)\s+languages?|'
    r'(?:друг|дополнительн|иностранн)\w*\s+язык',
    re.IGNORECASE,
)


def _in_optional_section(blob: str, pos: int) -> bool:
    """True, если позиция находится в разделе «желательно», а не «требования».

    Сравниваем, какой ЗАГОЛОВОК ближе слева. Встроенные в предложение обороты
    («… is nice to have») заголовками не считаются — иначе они гасят требования
    из следующих пунктов.
    """
    head = blob[:pos]
    opts = [m.start() for m in _OPTIONAL_SECTION.finditer(head)
            if _marker_is_header(head[:m.start()], m.group(0))]
    last_opt = max(opts, default=-1)
    last_req = max((m.start() for m in _REQUIRED_SECTION.finditer(head)), default=-1)
    return last_opt > last_req


# Описание на нечитаемом алфавите (грузинский, греческий, тайский, армянский,
# CJK). Требования «нужен местный язык» в тексте нет — оно самоочевидно для
# местного кандидата. Найдено 14.08.2026: вакансия TBC Capital целиком на
# грузинском проходила предфильтр с баллом 58, слова English в ней нет вообще.
_NON_READABLE_SCRIPT = re.compile(r"[^\x00-\x7FЀ-ӿ\s]")
_UNREADABLE_SHARE = 0.25


def _unreadable_script(blob: str) -> bool:
    """True, если описание преимущественно не на латинице и не на кириллице."""
    text = (blob or "").strip()
    if len(text) < 200:          # на коротком тексте доля скачет, не судим
        return False
    return len(_NON_READABLE_SCRIPT.findall(text)) / len(text) >= _UNREADABLE_SHARE


def _mention_is_optional(blob: str, start: int, end: int) -> bool:
    """True, если упоминание языка на [start:end] помечено как «плюс/желательно».

    Общая логика для гейта английского и гейта прочих языков — раньше она была
    продублирована и расходилась. Три случая, когда пометка НЕ относится к нашему
    языку (все найдены на живых вакансиях 14.08.2026):
      1. «English. Nice to have: …» — двоеточие, это заголовок следующего раздела;
      2. «English is required; additional languages are an advantage» — «плюс»
         относится к другому языку, названному поимённо или обобщённо;
      3. «…HubSpot will be a plus. Languages: fluency in English» — пометка из
         предыдущего предложения.
    """
    head = blob[max(0, start - 60): start]
    tail = blob[end: end + 60]

    plus = _LANG_PLUS_PATTERN.search(tail)
    # случай 1: пометка справа — заголовок СЛЕДУЮЩЕГО раздела, к нашему
    # требованию она не относится («…English, at C1 level or above. Nice to have …»)
    if plus and _marker_is_header(tail[:plus.start()], plus.group(0)):
        plus = None
    if plus:
        between = tail[:plus.start()]    # случай 2
        # «Russian AND/OR Greek would be considered an advantage» (RONIN) — наш
        # язык в ТОМ ЖЕ перечислении, что и помеченный плюсом, значит пометка
        # относится и к нему. Отличается от «Greek (written and verbal) and
        # German speakers is an advantage» (Teroxx), где после нашего языка
        # начинается новая конструкция, а не продолжение списка.
        if _LIST_CONTINUATION.match(between):
            return True
        return not (_FOREIGN_LANG_PATTERN.search(between)
                    or _OTHER_LANG_GENERIC.search(between))

    hp = _LANG_PLUS_PATTERN.search(head)  # случай 3
    return bool(hp) and not _SENTENCE_BREAK.search(head[hp.end():])


# Русские названия языков — ещё и топонимы, национальности и кухня. Стемы,
# добавленные 21.08.2026 ради «знание польскОГО языка», немедленно поймали
# «м. БелорусскАЯ» в описании офиса и зарубили релевантную вакансию
# Customer Success. Поэтому для КИРИЛЛИЧЕСКОГО совпадения требуем рядом
# слово про язык; на английскую половину это не распространяется — там
# «Ukrainian»/«Greek» в вакансиях почти всегда про язык.
_CYRILLIC = re.compile(r'[а-яё]', re.IGNORECASE)
_LANG_CONTEXT_RU = re.compile(
    r'(язык|владен|знан|уровн|разговорн|письменн|носител|общен|speak|fluen|proficien|native|level|[abc][12])',
    re.IGNORECASE)


def _foreign_lang_required(blob: str) -> bool:
    """True, если иностранный язык требуется (а не «будет плюсом»).

    Два уровня проверки: (1) пометка «плюс» рядом с самим языком;
    (2) язык внутри раздела «Preferred/Nice to have» — тоже не требование.
    """
    for m in _FOREIGN_LANG_PATTERN.finditer(blob):
        # Раньше «плюс» искался в окне ±60 без разбора, к чему он относится.
        # «professional fluency in English and Greek (written and verbal) and
        # German speakers IS AN ADVANTAGE» (Teroxx, 14.08.2026): пометка про
        # немецкий гасила обязательный греческий.
        if _mention_is_optional(blob, m.start(), m.end()):
            continue
        if _in_optional_section(blob, m.start()):
            continue
        # Кириллическое название без слова про язык рядом — это топоним или
        # национальность («м. Белорусская», «французский бульвар»), а не требование.
        if _CYRILLIC.search(m.group(0)):
            window = blob[max(0, m.start() - 60): m.end() + 60]
            if not _LANG_CONTEXT_RU.search(window):
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

# Какие уровни требования английского считать жёстким барьером.
# Строгость: spoken > generic > written. «written» по умолчанию НЕ режем —
# он закрывается переводчиком/AI, вместо отсева мягкий штраф (см. criteria.yaml).
_ENGLISH_GATE_LEVELS = set(CRITERIA.get("english_gate_levels", ["spoken", "generic"]))


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


# Уровень языка сплошь и рядом пишут КИРИЛЛИЦЕЙ: «Английский С1 и выше» — С здесь
# U+0421, а не латинская C, и шаблон `английский\s+c1` мимо неё проходит. Найдено
# 17.08.2026 на живой вакансии Synergy of Lake Technology: требование C1 гейт не
# увидел, вакансия набрала 66 баллов и ушла в AI.
#
# Заменяем ТОЛЬКО букву, за которой сразу идёт 1 или 2. Сплошная замена кириллицы
# на латиницу разнесла бы русский текст и русские же шаблоны: «скам» стало бы
# «cкам», и `распознавание скам` перестало бы совпадать.
_CYR_LEVEL_HOMOGLYPH = re.compile(r'[авс](?=[12](?![0-9]))')
_CYR_TO_LAT = {"а": "a", "в": "b", "с": "c"}


def _n(s: str | None) -> str:
    return _CYR_LEVEL_HOMOGLYPH.sub(
        lambda m: _CYR_TO_LAT[m.group(0)], (s or "").lower())


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

    if _unreadable_script(blob):
        return "описание не на латинице/кириллице — нужен местный язык"

    if _citizenship_barrier(blob):
        return "требуется гражданство/право на работу в ЕС или США"

    modality = _english_modality(blob)
    if modality in _ENGLISH_GATE_LEVELS:
        what = "разговорный" if modality == "spoken" else "свободный"
        return f"требуется {what} английский (у кандидата A1–A2)"

    if _night_shift_mode(blob) == "core":
        return "ночные смены как режим работы"

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
    # Структурное поле локации ВАЖНЕЕ случайного «remote» в тексте описания —
    # разбираем ДО начисления баллов. Fireblocks APAC (location=Singapore) получал
    # +10 за remote, потому что в описании было «remote troubleshooting»: это
    # техника поддержки, а не формат работы. Если локация явно называет город или
    # страну вне РФ и вне списка релокации — это офис там, куда кандидат не поедет.
    # Исключение: если в описании ЯВНО заявлен удалённый формат («fully remote»,
    # «полностью удалённо»), локация — это скорее юрадрес/штаб-квартира, и она
    # не должна перебивать формат. Одиночное слово «remote» таким сигналом не
    # считается — оно встречается в «remote troubleshooting», «remote access».
    loc_match = re.search(r"location:\s*([^\n]+)", blob)
    if loc_match and not _EXPLICIT_REMOTE.search(blob):
        loc_line = loc_match.group(1)
        if not _RU_LOCATION.search(loc_line) and not _hits(CRITERIA["relocation_ok"], loc_line)[0]:
            remote = False   # поле локации перевешивает «remote» из текста
            onsite = True

    if remote:
        score += _W["remote"]; reasons.append("remote")
    if reloc:
        score += _W["relocation"]; reasons.append("страна релокации подходит")

    if onsite and not remote and not reloc:
        # Офис в стране вне списка релокации — но если вакансия требует РУССКИЙ
        # язык, компания уже нанимает русскоязычных и обычно решает визу/переезд.
        # Такие рассматриваем: штраф мягче, вакансия остаётся видимой (решение
        # владельца 05.08.2026).
        if _hits(CRITERIA["english_boost"], blob)[0]:
            score += _W.get("onsite_ru_desk", -10)
            reasons.append("офис за рубежом, но команда/клиенты русскоязычные")
        else:
            score += _W["onsite"]; reasons.append("только офис в неподходящей локации")

    ew = role["english_weight"]
    nep, fep = _hits(CRITERIA["english_penalty"], blob)
    if nep:
        sub = int(nep * _W["english_penalty_each"] * ew)
        score += sub; reasons.append(f"{sub} требуется сильный английский: {', '.join(fep[:4])}")
    if _hits(CRITERIA["english_boost"], blob)[0]:
        add = int(_W["english_boost"] * ew)
        score += add; reasons.append(f"+{add} русскоязычная/CIS команда")

    # Требуется только ПИСЬМЕННЫЙ английский — барьер преодолим переводчиком/AI,
    # поэтому не режем, но помечаем и штрафуем: отклик всё равно потребует усилий,
    # да и собеседование, скорее всего, будет голосовым.
    _mod = _english_modality(blob)
    if _mod == "written" and "written" not in _ENGLISH_GATE_LEVELS:
        sub = int(_W.get("english_written_only", -8) * ew)
        score += sub
        reasons.append(f"{sub} нужен письменный английский (переводчик/AI — реально)")
    elif _mod == "level_b" and "level_b" not in _ENGLISH_GATE_LEVELS:
        sub = int(_W.get("english_level_b", -6) * ew)
        score += sub
        reasons.append(f"{sub} требуется английский B1–B2 (у кандидата A1–A2)")
    elif _mod == "level_c" and "level_c" not in _ENGLISH_GATE_LEVELS:
        sub = int(_W.get("english_level_c", -10) * ew)
        score += sub
        reasons.append(f"{sub} требуется английский C1–C2 без пометки «устный» "
                       f"(разрыв большой — не приоритет, но рассмотреть можно)")

    # Entry/junior/обучающие роли — целевой сегмент кандидата (низкий барьер)
    if _hits(CRITERIA.get("entry_boost", []), blob)[0]:
        score += _W["entry"]; reasons.append(f"+{_W['entry']} entry/junior (низкий барьер)")

    # Смены. Ночь как режим сюда не доходит — она отсечена в hard gate выше.
    shift_mode = _night_shift_mode(blob)
    if shift_mode == "occasional":
        sub = _W.get("night_shift_occasional", -15)
        score += sub
        reasons.append(f"{sub} редкие ночные смены (владелец их не хочет, "
                       f"но при сильной вакансии решает сам)")
    elif shift_mode == "shift":
        sub = _W.get("shift_schedule", -8)
        score += sub
        reasons.append(f"{sub} сменный график")

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
    """Скорит вакансию по всем ролям из criteria.yaml, возвращает лучшую по баллу.

    Локация подмешивается в текст: она хранится ОТДЕЛЬНЫМ полем, и раньше скоринг
    её не видел вообще. Из-за этого офисные вакансии в неподходящих странах
    (реальный случай 05.08.2026: Fireblocks «Technical Support Engineer, APAC»,
    location=Singapore) не получали штрафа — в описании страна не упоминалась.
    """
    text = job.description
    if job.location:
        text = f"{text}\nLocation: {job.location}"
    results = [score_vacancy(job.title, text, r) for r in CRITERIA["roles"]]
    best = max(results, key=lambda r: r["score"])
    return {"best": best, "all": results}


def passes_pre_filter(job: Job) -> bool:
    """Обёртка для scheduler.py — true если вакансия прошла гейт и достигла порога."""
    best = score_job(job)["best"]
    return best["passed_gate"] and best["recommend"]
