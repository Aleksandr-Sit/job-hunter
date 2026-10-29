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
# Два слова из списков — ещё и обычные слова других языков (13.10.2026, LinkedIn):
# - «go» — английский глагол: «help companies BUILD stronger businesses — helping
#   them GO from doing digital…» (Cognizant, Video Content Moderator with Russian)
#   резалось как «требуется писать код». Язык Go пишут как «golang» или ловят по
#   титулу «Go Developer» (dev-гейт заголовка).
# - «jest» — польское «есть/является»: любая польская вакансия (Ruukki, Technical
#   Support Specialist) резалась как требующая JS-фреймворк. Считаем фреймворком,
#   только если рядом слова про тесты.
_CODE_REQUIRED_PATTERN = re.compile(
    r'(?:'
    r'\b(?:selenium|cypress|playwright|pytest|mocha|appium|robot\s+framework)\b'
    r'|\bjest\b(?=.{0,40}\b(?:tests?|testing|unit|framework|enzyme)\b)'
    r'|\b(?:tests?|testing|unit|framework)\b.{0,40}\bjest\b'
    r'|\b(?:kubernetes|k8s|helm|terraform|ansible|ci/cd|jenkins|github\s+actions)\s+(?:experience|required|skills?|знани)'
    r'|\b(?:write|develop|code|program|implement|build)\b.{0,40}\b(?:python|javascript|typescript|rust|golang|solidity|java|c\+\+)\b'
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
# Возраст кандидата, а не стаж: «Возраст: от 18 лет», «от 18 лет и старше».
_AGE_CONTEXT = re.compile(r'возраст|полных\s+лет|\bage\b|лет\s+и\s+старше', re.IGNORECASE)


def _high_exp_required(blob: str) -> bool:
    """True, если требуется 7+ лет опыта (а не просто упомянуто «N лет»).

    Две ловушки, обе ловились до 17.09.2026 и роняли вакансию в ноль:
    - «Возраст от 18 лет» — явная нижняя граница, но это ВОЗРАСТ, а не стаж;
    - «founded over 15 years ago» — история компании. Проверка на историю была,
      но только во второй ветке: явная граница («over 15 years») проскакивала
      мимо неё.
    """
    for m in _EXPLICIT_HIGH_EXP.finditer(blob):
        window = blob[max(0, m.start() - 40): m.end() + 40]
        if _AGE_CONTEXT.search(window) or _EXP_HISTORY_CONTEXT.search(window):
            continue
        return True
    for m in _YEARS_NUM_PATTERN.finditer(blob):
        window = blob[max(0, m.start() - 40): m.end() + 40]
        if _AGE_CONTEXT.search(window):
            continue
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
    r'eu work permit|eu work authori[sz]ation|'
    r'гражданство ес|паспорт ес|гражданство сша)\b',
    re.IGNORECASE,
)
# «right/eligible/authorized to work in X» — дисквалифицирует, только если X не РФ
# (иначе отсекли бы обычные российские вакансии «право работать в РФ»).
#
# «Valid full-time work AUTHORIZATION FOR Greece» (Workster, 29.10.2026), «non-EU
# residents must have a valid work permit in Greece», «holder of a work permit for
# Greece». Само «work permit for X» требованием не считаем: «we help with your work
# permit in Cyprus» — это помощь с визой, плюс для кандидата. Нужен признак того,
# что разрешение уже должно быть: valid / hold / holder of / must have.
_RIGHT_TO_WORK = re.compile(
    r'(?:right to work|eligible to work|authoriz(?:ed|ation) to work|legally able to work|'
    r'(?:valid|hold|holds|holder\s+of|existing|current|possess\w*|already\s+have|must\s+have)'
    r'\s+(?:a\s+|an\s+)?(?:[\w-]+\s+){0,3}?'
    r'(?:work\s+(?:authori[sz]ation|permit)|residence\s+and\s+work\s+permit)'
    r'(?:\s*/\s*right\s+to\s+work)?)'
    r'\s+(?:in|for)\s+(?:the\s+)?([a-zа-я\s]{2,20})',
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
# Строка формата работы, которую hh_enrich берёт из поля workFormats страницы
# (в тексте после _n — нижний регистр: «формат работы (hh): on_site, hybrid»).
_HH_WORK_FORMAT = re.compile(r"формат работы \(hh\):\s*([a-z_, ]+)")
# Город кандидата: офис здесь — не переезд, штраф отдельный (onsite_home).
_HOME_CITY = re.compile(r"самар|samara")

_EXPLICIT_REMOTE = re.compile(
    r'(fully[\s-]remote|100%\s*remote|remote[\s-]first|work from anywhere|'
    r'remote position|remote role|fully distributed|'
    r'полностью удалённ|полностью удаленн|удалённая работа|удаленная работа|'
    r'удалённый формат|удаленный формат)',
    re.IGNORECASE,
)

# ── 3a. Офис или гибрид в ДРУГОМ городе — жёсткий отсев ───────────────────────
# Решение владельца 26.10.2026 (отменяет его же решение 25.09 «исключительный оффер
# хочу видеть»): цель — удалёнка и свободный график под тренировки, переезжать в
# другой город РФ ради офиса он не будет. Штраф −10 (`onsite_ru_city`) не держал
# такие вакансии: AI ставил им 80+, и за 22.09–26.10 их ушло в Telegram 291 из 888 —
# треть потока (замер data/scans/schedule_audit_2026-10-26.md).
#
# Режем только по ПОЛЮ формата hh (строка hh_enrich): слово «офис» в тексте ничего
# не решает. Не режем: REMOTE среди форматов (в том числе «на месте, удалённо,
# гибрид» сразу — 72 такие в наборе), Самару, страны релокации, пустую локацию
# (там остаётся штраф) и текст, ЯВНО обещающий удалёнку.
#
# Обещание удалёнки — только без отрицания и без «частично»: в наборе 10 офисных
# вакансий с такой фразой, и шесть из них её отрицают или сужают: «(не удаленный
# формат)», «удаленный формат не предусмотрен!», «не предлагаем частичную, проектную
# занятость, а так же удаленный формат работы», «1 день по выбору - удаленный формат»,
# «Удаленная работа: опыт удаленной поддержки AnyDesk» (навык, а не формат).
_REMOTE_CLAUSE_START = re.compile(r"[.;!?\n•·|()]")
_REMOTE_NEG_LEFT = re.compile(r"\b(?:без|нет|не|никак\w*|no|not)\b")
_REMOTE_PARTIAL_LEFT = re.compile(
    r"частичн\w*|гибрид\w*|\b[1-4]\s*(?:дн|день|раз)\w*|partial\w*|hybrid")
_REMOTE_NEG_RIGHT = re.compile(
    r"^[^.;!?\n]{0,6}\s*(?:не\s+(?:предусмотрен|возмож|рассматрива|практику|предоставля)|"
    r"невозмож|исключ|отсутству|нет\b|not\s+(?:possible|available|offered))")
# «Удаленная работа: опыт удаленной поддержки» — заголовок навыка в требованиях
_REMOTE_SKILL_RIGHT = re.compile(r"^\s*:\s*(?:опыт|навык|знани|умени|experience)")
# «можно работать удаленно 1 день в неделю» — число дней СПРАВА, тоже гибрид
_REMOTE_PARTIAL_RIGHT = re.compile(
    r"^[^.;!?\n]{0,12}?\b[1-4]\s*(?:дн|день|раз\w*\s+в\s+недел)")
# Обещания удалёнки, которых нет в общем `_EXPLICIT_REMOTE` (его же зовут
# normalize.detect_remote и штраф зарубежного офиса — туда не добавлял, чтобы не
# менять их поведение). Замер 27.10.2026 на наборе 888: без них ложно резались
# Apyflow «работаем удалённо… Удалённо из любой точки», «Специалист по внедрению
# ИИ» «возможность удаленного формата работы с командировками в Москву» и «Возможен
# вариант удаленной работы, после испытательного срока». Голое «удалённо» не беру:
# «решение инцидентов на месте и удаленно», «пользователей, работающих удалённо» —
# обязанности, а не формат. Голое «удалёнка» тоже не беру: в наборе оно только в
# гибриде («офис в Москве + удаленка», «пн-пт в офисе, сб-вс удаленка»). По просьбе
# владельца 27.10 добавлены «перевод/переход на удалённую работу (удалёнку)»,
# «возможна работа удалённо», «возможность работать из дома» — с «возмож…», чтобы не
# ловить теги hh «работа из дома, офис, работа в офисе».
_REMOTE_PROMISE_MORE = re.compile(
    r"работаем\s+удал[её]нно|удал[её]нно\s+из\s+любой\s+точки|"
    r"(?:можно|можешь|можете)\s+работать\s+(?:полностью\s+)?удал[её]нно|"
    r"возмож\w*\s+(?:вариант\s+)?удал[её]нн\w*\s+(?:формат|работ)\w*|"
    r"(?:перевод|переход)\w*\s+на\s+(?:удал[её]нн\w*\s+(?:работ|формат)\w*|удал[её]нк\w*)|"
    r"возмож\w*\s+работ\w*\s+(?:удал[её]нно|из\s+дома)")


def _remote_promised(blob: str) -> str | None:
    """Фраза, которой текст ЯВНО обещает удалённый формат, или None.

    `blob` в нижнем регистре. Слева смотрим только свою часть фразы (до 60
    символов, до точки или скобки): отрицание дальше — уже про другое."""
    hits = sorted([*_EXPLICIT_REMOTE.finditer(blob), *_REMOTE_PROMISE_MORE.finditer(blob)],
                  key=lambda m: m.start())
    for m in hits:
        left = blob[max(0, m.start() - 60): m.start()]
        cut = list(_REMOTE_CLAUSE_START.finditer(left))
        left = left[cut[-1].end():] if cut else left
        right = blob[m.end(): m.end() + 40]
        if _REMOTE_NEG_LEFT.search(left) or _REMOTE_PARTIAL_LEFT.search(left):
            continue
        if (_REMOTE_NEG_RIGHT.search(right) or _REMOTE_SKILL_RIGHT.search(right)
                or _REMOTE_PARTIAL_RIGHT.search(right)):
            continue
        return m.group(0)
    return None


def _office_elsewhere(blob: str) -> str | None:
    """Город, если по полю hh вакансия только офисная/гибридная и не в Самаре.

    None — не режем: формата hh нет (вакансия не дообогащена или не с hh), среди
    форматов есть REMOTE, локации нет, локация — Самара или страна релокации,
    либо текст явно обещает удалёнку."""
    hh_fmt = _HH_WORK_FORMAT.search(blob)
    if not hh_fmt:
        return None
    formats = set(re.findall(r"[a-z_]+", hh_fmt.group(1)))
    if not formats or "remote" in formats:
        return None
    loc = re.search(r"location:\s*([^\n]+)", blob)
    city = loc.group(1).strip() if loc else ""
    if not city or _HOME_CITY.search(city):
        return None
    if _hits(CRITERIA["relocation_ok"], city)[0]:
        return None
    if _remote_promised(blob):
        return None
    return city

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
    # «Russian C1 and VERY GOOD English» (Workster, Афины, 29.10.2026), «Good
    # written and verbal English», «Good oral and written English is essential».
    # Голого «good» в шаблонах выше не было. Между good и English — только слова
    # про сам язык: «a good chance to learn English» — это не требование, а
    # «English-speaking partners» — про клиентов, а не про уровень кандидата.
    r'(?:very\s+)?good\s+'
    r'(?:(?:spoken|written|verbal|oral|business|working|conversational|and|or|both|'
    r'level|command|knowledge|understanding|proficiency|skills?|of|the|in)[\s,/&]+){0,5}'
    r'english\b(?![\s-]*speak)|'
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
# Наш язык — продолжение перечисления, начатого сразу после него.
# Только союз, НЕ запятая: «English, Georgian languages are preferred» (Epson) —
# запятая начинает новую мысль о другом языке, а «Russian AND/OR Greek» (RONIN)
# — одно перечисление, и пометка «плюс» относится ко всему списку.
_LIST_CONTINUATION = re.compile(r'^\s*(?:and\s*/?\s*or|and|or|или|и)\s+',
                                re.IGNORECASE)

# Упоминание русского/СНГ. Гейт английского его больше не читает (29.10.2026),
# но им сортирует очередь LinkedIn title_triage — один источник правды.
_RU_MENTION = re.compile(r'\brussian\w*|русск\w*|\bcis\b|\bснг\b', re.IGNORECASE)

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
    «желательно».

    Русскоязычная вакансия исключением НЕ является (решение владельца 29.10.2026).
    До этого «Russian-speaking» в тексте отключал проверку английского целиком: «там
    английский вторичен, решение за кандидатом». Повод отменить — Workster (Афины):
    «Russian-Speaking Sales Specialist», в требованиях «Russian C1 and very good
    English», карточка ушла с баллом 80. Если работодатель требует английский
    прямо, это требование и для русскоязычной роли. «English is a plus» и уровни
    B/C по-прежнему не режутся (пометка плюса и штрафы ниже). Замер на батче 4838:
    338 → 325, все тринадцать — с явным fluent/strong/good English.
    """
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


# Отрицание рядом с ночными сменами. «Без ночных смен» и «ночных смен нет» —
# это ПЛЮС вакансии, а до 17.09.2026 они роняли её с 72 до 0: regex видел только
# «ночн* смен» и не смотрел, что стоит вокруг.
_NIGHT_NEG_BEFORE = re.compile(
    r"(?:без|не\s+предусмотрен\w*|отсутству\w*|no|without|never)\W{0,12}$",
    re.IGNORECASE,
)
# `\w*` в начале: regex ловит «ночные смен», а отрицание стоит после окончания —
# «ночные смены НЕ предусмотрены».
_NIGHT_NEG_AFTER = re.compile(
    r"^\w*\W{0,12}(?:нет|не\s+предусмотрен\w*|отсутству\w*|исключен\w*|не\s+будет)",
    re.IGNORECASE,
)


def _night_negated(blob: str, match: re.Match) -> bool:
    """True, если рядом с «ночными сменами» стоит отрицание."""
    before = blob[max(0, match.start() - 30): match.start()]
    after = blob[match.end(): match.end() + 30]
    return bool(_NIGHT_NEG_BEFORE.search(before) or _NIGHT_NEG_AFTER.search(after))


def _night_shift_mode(blob: str) -> str | None:
    """Как в вакансии устроены смены: 'core' | 'occasional' | 'shift' | None.

    'core'       — ночь штатный режим -> жёсткий отсев;
    'occasional' — считанные ночи в месяц -> штраф;
    'shift'      — сменный график без ночей -> меньший штраф (а если смены —
                   единственный режим, вакансию режет `_rigid_schedule`).
    """
    if _NIGHT_OCCASIONAL.search(blob):
        return "occasional"
    if any(not _night_negated(blob, m) for m in _NIGHT_CORE.finditer(blob)):
        return "core"
    if any(not _shift_negated(blob, m) for m in _SHIFT_SCHEDULE.finditer(blob)):
        return "shift"
    return None


# Отрицание рядом со сменами — по образцу `_night_negated`: «без сменного графика»,
# «не посменная работа», «сменного графика нет», «no shift work». До 26.10.2026 его
# не было: отрицание давало −8, как сами смены. Голое «не» — только вплотную:
# `_SHIFT_SCHEDULE` ловит «посменн» и внутри «НЕпосменная».
_SHIFT_NEG_BEFORE = re.compile(
    r"(?:\bбез|\bнет|\bне|\bникак\w*|\bno|\bnot|\bwithout|\bnever)\W{0,12}$",
    re.IGNORECASE,
)
_SHIFT_NEG_AFTER = re.compile(
    r"^\w*\W{0,12}(?:нет\b|не\s+предусмотрен\w*|отсутству\w*|исключен\w*|не\s+будет|"
    r"не\s+требу\w*|not\s+required|is\s+not)",
    re.IGNORECASE,
)


def _shift_negated(blob: str, match: re.Match) -> bool:
    """True, если рядом со «сменным графиком» стоит отрицание."""
    before = blob[max(0, match.start() - 30): match.start()]
    after = blob[match.end(): match.end() + 30]
    return bool(_SHIFT_NEG_BEFORE.search(before) or _SHIFT_NEG_AFTER.search(after))


# ── 3b'. Сменный график как РЕЖИМ — жёсткий отсев ────────────────────────────
# Решение владельца 26.10.2026: новая цель — свободный график под тренировки по
# пляжному волейболу, поэтому смены, 6/1, 12-часовые и ночные смены режутся, а не
# штрафуются −8. Штраф не держал: AI ставил таким 80+. За 22.09–26.10 таких
# ушло в Telegram 41 из 888, и 24 из них были видны ТОЛЬКО в полях hh (строки
# hh_enrich._schedule_note), в тексте — ни слова: OsteoFace 6/1, hh 137767979.
#
# Режем, если смены — единственный режим:
#   - график hh непустой и весь из сменных (2/2, 3/3, 6/1, по выходным…);
#   - часы hh непустые и только 10/11/12/24;
#   - ночные смены по полю hh, а свободного графика или часов нет и выбора тоже нет.
#     Выбор — 5/2 или 4/3 наряду со сменами: «2/2 или 5/2, смены по 6, 9 или 12 часов,
#     доплата 50% за работу в ночь», «5/2, 2/2 в день, 2/2 в ночь». Ночь там — одна
#     из опций; замер 27.10 на наборе 888: шесть из семи срезанных по полю были такими,
#     а «5/2 или 2/2 на выбор» владелец велел не трогать;
#   - графика hh нет (LinkedIn, ATS, Telegram) — фраза о сменах без отрицания и без
#     альтернативы в тексте (5/2, пятидневка, гибкий/свободный график, flexible).
# НЕ режем, если в графике hh есть 5/2, 4/3, свободный или «другой» наряду со
# сменами — там остаётся штраф `shift_schedule`. Restik (hh 138178885): «сменный
# график с 11:00 до 19:00», а в поле — «другой»; это дневная работа, она остаётся.
#
# Строки hh — коды в нижнем регистре: «график (hh): two_on_two_off».
_HH_SCHEDULE = re.compile(r"график \(hh\):\s*([a-z0-9_, ]+)")
_HH_HOURS = re.compile(r"часы в день \(hh\):\s*([a-z0-9_, ]+)")
_HH_NIGHT = re.compile(r"работа ночью \(hh\):\s*да")
_RIGID_DAYS = frozenset({
    "two_on_two_off", "three_on_three_off", "four_on_four_off", "four_on_two_off",
    "three_on_two_off", "two_on_one_off", "one_on_three_off", "one_on_two_off",
    "six_on_one_off", "weekend",
})
_LONG_HOURS = frozenset({"hours_10", "hours_11", "hours_12", "hours_24"})
_DAY_ALTERNATIVE = frozenset({"five_on_two_off", "four_on_three_off"})   # 5/2, 4/3
_DAYS_LABEL = {
    "two_on_two_off": "2/2", "three_on_three_off": "3/3", "four_on_four_off": "4/4",
    "four_on_two_off": "4/2", "three_on_two_off": "3/2", "two_on_one_off": "2/1",
    "one_on_three_off": "1/3", "one_on_two_off": "1/2", "six_on_one_off": "6/1",
    "weekend": "по выходным",
}
# Альтернатива сменам в том же тексте: «график 5/2 или 2/2 на выбор», «сменный
# или гибкий график». С ней вакансия не режется — остаётся штраф.
# Диапазон будних дней — тоже пятидневка: «Shift schedule: Monday-Friday 9am-6pm»,
# «Shift work: Sunday-Thursday» (рабочая неделя ОАЭ) — слово shift там про часы, а
# не про сменный режим (проверено 27.10.2026 перед замером на батче).
_WEEKDAY_RANGE = (
    r"\b(?:monday|mon)\W{1,5}(?:to\W{1,3}|through\W{1,3})?(?:friday|fri)\b|"
    r"\b(?:sunday|sun)\W{1,5}(?:to\W{1,3}|through\W{1,3})?(?:thursday|thu)\b|"
    r"понедельник\w*\W{1,5}(?:по\W{1,3})?пятниц\w*|\bпн\W{1,3}пт\b|"
    r"воскресень\w*\W{1,5}(?:по\W{1,3})?четверг\w*")
_SHIFT_ALTERNATIVE = re.compile(
    r"\b5\s*(?:/|х|x|на|через)\s*2\b|пятидневк\w*|пятидневн\w*|"
    r"гибк\w*\s+(?:график|режим|начал|рабоч\w*\s+(?:день|врем|час))|"
    r"свободн\w*\s+(?:график|режим)|\bflexib|" + _WEEKDAY_RANGE,
    re.IGNORECASE,
)


def _hh_codes(rx: re.Pattern, blob: str) -> set[str]:
    m = rx.search(blob)
    return set(re.findall(r"[a-z0-9_]+", m.group(1))) if m else set()


def _rigid_schedule(blob: str) -> str | None:
    """Почему смены — единственный режим работы, или None (см. шапку раздела)."""
    days = _hh_codes(_HH_SCHEDULE, blob)
    hours = _hh_codes(_HH_HOURS, blob)
    if days and days <= _RIGID_DAYS:
        return "график hh " + ", ".join(sorted(_DAYS_LABEL[d] for d in days))
    if hours and hours <= _LONG_HOURS:
        return "смена hh " + ", ".join(sorted(h[6:] for h in hours)) + " ч"
    day_choice = len(days) > 1 and bool(days & _DAY_ALTERNATIVE)
    if _HH_NIGHT.search(blob) and "flexible" not in days | hours and not day_choice:
        return "ночные смены по полю hh"
    if days:
        return None     # в графике hh есть 5/2, 4/3, свободный или «другой»
    for m in _SHIFT_SCHEDULE.finditer(blob):
        if _shift_negated(blob, m):
            continue
        if _SHIFT_ALTERNATIVE.search(blob):
            return None
        return f"«{m.group(0)}»"
    return None


# ── 3c. Холодный поиск клиентов ───────────────────────────────────────────────
# Решение владельца 18.10.2026: продажи, где сотрудник САМ ищет компании и звонит
# им, — жёсткий отсев в любой роли (лучшая роль берётся по максимуму, поэтому отсев
# только в sales_remote пропустил бы «Менеджера по работе с клиентами» через
# поддержку). Входящие заявки, тёплая база, действующие клиенты — не трогаем.
# Штраф −12 в sales_remote не работал: история бота — 506 вакансий ушли в Telegram
# с баллом 55–88, хотя AI сам писал «требует 20–30 холодных звонков в день».
#
# Только ЯВНЫЕ формулировки: ошибка «пропустили» оставляет вакансию AI (как было),
# ошибка «срезали» прячет её навсегда. Не взяты:
#   «привлекать новых клиентов» — цель отдела, а не способ: так пишет и вакансия
#     «без холодных звонков» (Fintech/Payments, hh 138043438);
#   «выход на ЛПР» — навык и в продажах ДЕЙСТВУЮЩИМ клиентам (факторинг, hh 138179571);
#   «привлечение клиентов», «лидогенерация» — маркетологи;
#   голое «outbound» — логистика, «inbound, outbound and partner-sourced»;
#   «cold email» — поддержка платформы холодных писем (QuickMail);
#   «исходящие звонки», «обзвон базы», «звонки по базе» — перезвон по заявкам;
#   «поиск новых клиентов» без «активный/самостоятельный» — строка в списке
#     обязанностей аккаунт-менеджера; остаётся мягким штрафом sales_remote.
_COLD_OUTREACH = re.compile(
    # холодные звонки / обзвон / продажи / база / отдел / рассылки
    r'\bхолодн\w*\s+(?:и\s+т[её]пл\w*\s+|/\s*т[её]пл\w*\s+)?'
    r'(?:звонк|обзвон|прозвон|продаж|поиск|баз|контакт|клиент|привлечени|отдел|рассылк)\w*|'
    # активный / самостоятельный поиск клиентов
    r'\b(?:активн|самостоятельн)\w*\s+(?:поиск|привлечени|привлека|иска|наход)\w*\s+'
    r'(?:и\s+привле[кч]\w*\s+)?(?:новых\s+)?(?:клиент|компани|заказчик|партн[её]р)\w*|'
    # «сам находит клиентов», «самостоятельно формировать базу»
    r'\b(?:сам|сама|сами|самостоятельно)\s+(?:наход|иска|ищ|привлека|формирова)\w*'
    r'(?:\s+и\s+\w+)?\s+(?:(?:новых|потенциальных|прямых|целевых)\s+)?'
    r'(?:клиент|заказчик|партн[её]р|компани|баз|рекламодател|мерчант)\w*|'
    # «искать новых клиентов», «выявлять потенциальных заказчиков»
    r'\b(?:искать|находить|находит|находят|ищет|выявлять)\s+(?:и\s+привлекать\s+)?'
    r'(?:новых|потенциальных|прямых)\s+(?:[\w]+-)?'
    r'(?:клиент|заказчик|партн[её]р|компани|рекламодател|мерчант)\w*|'
    r'\bпоиск\w*\s+(?:и\s+привлечени\w*\s+)?потенциальных\s+(?:клиент|заказчик|партн[её]р)\w*|'
    r'\bпо\s+базе\s+потенциальных|'
    r'\bкто\s+не\s+жд[её]т\s+(?:вашего\s+|твоего\s+)?звонк|'
    r'\b(?:про|об)ходить\s+секретар|'
    r'\bcold[\s-]?call\w*|\bcold\s+(?:outreach|prospecting|leads?)\b',
    re.IGNORECASE,
)

# План звонков: «80 дозвонов в день», «ежедневно по 20-30 звонков», «120+ cold
# calls per day». Входящая линия считает звонки тоже — её отличает «входящ» рядом.
_CALL_QUOTA = re.compile(
    r'\b\d{2,3}\s*(?:\+|[-–—]\s*\d{2,3})?\s*'
    r'(?:исходящих\s+|холодных\s+|результативных\s+|успешных\s+)?'
    r'(?:звонк\w*|дозвон\w*|наборов|вызовов)\s+'
    r'(?:в\s+день|в\s+смену|ежедневно|за\s+день|в\s+сутки)|'
    r'\bежедневно\s+(?:\w+\s+){0,2}?по\s+\d{2,3}\s*(?:[-–—]\s*\d{2,3})?\s*(?:звонк|дозвон)\w*|'
    r'\b\d{2,3}\s*\+?\s*(?:cold\s+|outbound\s+)?(?:calls|dials)\s+(?:per|a|each)\s+day',
    re.IGNORECASE,
)

# Отрицание в той же фразе: «Без холодных звонков», «никакого холодного поиска»,
# «без необходимости строить продажи на холодном поиске», «не холодный обзвон»,
# «холодного поиска новых клиентов нет», «…в задачи позиции не входят».
# «Не бойтесь холодных звонков» — НЕ отрицание: звонки как раз есть.
_COLD_NEG_BEFORE = re.compile(
    r'\b(?:без|нет|никак\w*|не\s+надо|'
    r'не\s+(?:нужн|требу|будет|делае|делаю|занима|использу|придет|придёт|звони)\w*|'
    r'отсутству\w*|исключ\w*|no|not|without|zero|never)\b|\bне\s+(?:с\s+|со\s+)?$',
    re.IGNORECASE,
)
_COLD_NEG_AFTER = re.compile(
    r'^[^.;!?\n•·|]{0,60}?(?:\bнет\b|\bне\s+(?:нужн|надо|требу|будет|предусм|делае|'
    r'входят|входит|придется|придётся)|отсутству|исключ|not\s+required|not\s+needed)',
    re.IGNORECASE,
)
_COLD_NOT_NEG = re.compile(r'бо[яи]\w*|страш\w*|пуга\w*|afraid', re.IGNORECASE)
# Описание продукта, а не обязанность: «ПО, которое помогает бизнесу находить
# новых клиентов» (hh 138285528) — так пишет и CRM-компания с продажами на входящих.
_COLD_PRODUCT = re.compile(r'помога\w*\s+(?:\w+\s+){0,2}$', re.IGNORECASE)
_CLAUSE_END = re.compile(r'[.;!?\n•·|]')


def _cold_negated(blob: str, m: re.Match) -> bool:
    """True — совпадение не про обязанность: отрицание или описание продукта."""
    left = blob[max(0, m.start() - 60): m.start()]
    cut = list(_CLAUSE_END.finditer(left))
    left = left[cut[-1].end():] if cut else left
    if _COLD_PRODUCT.search(left):
        return True
    if _COLD_NOT_NEG.search(left):
        return False
    after = blob[m.end(): m.end() + 70]
    return bool(_COLD_NEG_BEFORE.search(left) or _COLD_NEG_AFTER.search(after))


def _cold_outreach(blob: str) -> str | None:
    """Фраза, по которой видно холодный поиск клиентов, или None."""
    for m in _COLD_OUTREACH.finditer(blob):
        if not _cold_negated(blob, m):
            return m.group(0)
    for m in _CALL_QUOTA.finditer(blob):
        if "входящ" in blob[max(0, m.start() - 30): m.end()].lower():
            continue
        if not _cold_negated(blob, m):
            return m.group(0)
    return None


# ── 3d. Работодатель ждёт, что сотрудник САМ пользуется ИИ ────────────────────
# Решение владельца 26.10.2026: цель — расти в ИИ, поэтому вакансия, где ИИ —
# рабочий инструмент сотрудника («используете ChatGPT и Claude в работе», «готовность
# применять нейросети»), получает пометку в карточке и прибавку `ai_tools` (вес в
# criteria.yaml; с 27.10.2026 — 0 по замеру: прибавка тащила к AI только чужие роли).
# «Мы разрабатываем AI-платформу» (ИИ в продукте) и «опыт проектов AI/ML» (ИИ в
# опыте) — не то: там ИИ — предмет работы, а не инструмент. В наборе 888 карточек
# 22.09–26.10 первое — 123 вакансии, из них 57 не AI-ролей.
#
# Слово-инструмент + глагол использования в окне ±70 символов, а не «в предложении»:
# в hh списки часто без точек, и предложение растягивается на весь блок обязанностей.
# Логика — `ai_tools_phrase` из data/scans/schedule_audit_tools/sched_classify.py
# (вид «tools»), проверена глазами на этом наборе.
# «агент», но не «агентство»: «Команда AI-агентства GINAI ищет менеджера…» (hh
# 137880718) получала пометку — ИИ там продукт агентства, а не инструмент сотрудника.
_AI_WORD = re.compile(
    r"(?:\b(?:ai|ии)\b[- ]?(?:инструмент|ассистент|сервис|агент(?!ств)|tool|решени)\w*|нейросет\w*|"
    r"нейронн\w+\s+сет\w*|искусственн\w+\s+интеллект\w*|chat\s*gpt|\bgpt\b|claude|cursor|copilot|"
    r"\bllm\b|генеративн\w+|\bai[- ]?(?:native|first)\b|промпт\w*|\bprompt\w*|deepseek|gigachat|"
    r"гигачат|yandexgpt|perplexity|\b(?:ai|ии)\b)",
    re.IGNORECASE,
)
_AI_USE = re.compile(
    r"использ\w*|примен\w*|владе\w*|работ\w*\s+с\b|умее\w*|умени\w*|опыт\w*|интерес\w*|"
    r"увлека\w*|люб\w*|знаком\w*|навык\w*|приветству\w*|плюс\w*|преимуществ\w*|ежедневн\w*|"
    r"в\s+(?:своей\s+)?работе|ускор\w*|не\s+бо\w+|открыт\w*|готов\w*|\buse\b|\busing\b|"
    r"leverag\w*|experience\s+with|comfortable",
    re.IGNORECASE,
)
# «Мы / наша компания / платформа …» перед словом про ИИ — это про продукт
_AI_SELF = re.compile(
    r"\b(?:мы|наш\w*|компани\w*|продукт\w*|платформ\w*|разрабатыва\w*|создаём|создаем|we|our)\b",
    re.IGNORECASE,
)
_AI_SELF_BUT_USE = re.compile(r"использ|примен|владе|интерес|умее|умени|навык", re.IGNORECASE)
_AI_TOOL = re.compile(
    r"(?:\b(?:ai|ии)\b[- ]?(?:инструмент|ассистент|сервис|агент(?!ств)|tool|помощник)\w*|нейросет\w*|"
    r"chat\s*gpt|\bgpt\b|claude|cursor|copilot|deepseek|gigachat|гигачат|yandexgpt|perplexity|"
    r"midjourney|промпт\w*|\bprompt\w*|"
    r"(?:использ\w*|примен\w*|с\s+помощью|помощью|работ\w*\s+с)\s+(?:\w+\s+){0,2}(?:ai|ии)\b"
    r"(?!-(?:продукт|платформ|сфер|ниш|направлен|рынк|стартап|компан|проект)))",
    re.IGNORECASE,
)
# Хвост `(?!-продукт…)`: «работы с AI-продуктами» — это ИИ как товар, который
# продают, а не инструмент. Набор 888 (27.10.2026): так помечались 5 вакансий продаж
# ИИ-продуктов, а «навыки работы с генеративными AI-моделями» — пользование ИИ, остаётся.
_AI_TOOL_USE = re.compile(
    r"использ\w*|примен\w*|владе\w*|работ\w*\s+с\b|интерес\w*|готовн\w*|с\s+помощью|ежедневн\w*|"
    r"в\s+(?:своей\s+)?работе|умени\w*|умее\w*|навык\w*|увлека\w*|\buse\b|\busing\b|leverag\w*|"
    r"comfortable",
    re.IGNORECASE,
)
_AI_CLAUSE_END = re.compile(r"[.;!?\n•·|]")
_AI_WINDOW = 70      # окно решения — как в разборе 26.10.2026
_AI_REACH = 100      # сколько фразы показывать в каждую сторону без точки


def _clause_left(text: str, i: int) -> int:
    lo = max(0, i - _AI_REACH)
    ends = [m.end() for m in _AI_CLAUSE_END.finditer(text, lo, i)]
    if ends:
        return ends[-1]
    if lo > 0 and text[lo - 1].isalnum():      # не начинать с огрызка слова
        sp = text.find(" ", lo, i)
        return sp + 1 if sp >= 0 else lo
    return lo


def _clause_right(text: str, i: int) -> int:
    hi = min(len(text), i + _AI_REACH)
    m = _AI_CLAUSE_END.search(text, i, hi)
    if m:
        return m.start()
    if hi < len(text) and text[hi].isalnum():
        sp = text.rfind(" ", i, hi)
        return sp if sp >= 0 else hi
    return hi


def ai_tools_phrase(text: str | None) -> str | None:
    """Фраза, по которой видно, что сотрудник сам пользуется ИИ, или None.

    Решение — по окну ±70 символов вокруг слова про ИИ. Показывается фраза (до
    точки или пункта списка) вокруг слова-инструмента и ближайшего к нему глагола
    использования: «Используете в работе: • ChatGPT» — глагол в заголовке списка."""
    text = text or ""
    for w in _AI_WORD.finditer(text):
        lo = max(0, w.start() - _AI_WINDOW)
        hi = min(len(text), w.end() + _AI_WINDOW)
        win = text[lo:hi]
        if not _AI_USE.search(win):
            continue
        if (_AI_SELF.search(text[max(0, w.start() - 40):w.start()])
                and not _AI_SELF_BUT_USE.search(win)):
            continue
        tool = _AI_TOOL.search(text, lo, hi)
        uses = list(_AI_TOOL_USE.finditer(text, lo, hi))
        if not (tool and uses):
            continue
        use = min(uses, key=lambda m: abs(m.start() - tool.start()))
        a = min(_clause_left(text, tool.start()), _clause_left(text, use.start()))
        b = max(_clause_right(text, tool.end()), _clause_right(text, use.end()))
        return re.sub(r"\s+", " ", text[a:b]).strip(" ,;:-—–•·")
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
    r'advantage|beneficial|preferred|bonus|плюсом|преимуществ|приветствуется|желательно|'
    # «French or Chinese HIGHLY VALUED» (ExoClick, 13.10.2026) — не требование.
    # Функция общая с гейтом английского, поэтому только устойчивые обороты:
    # голое «appreciated» («your work will be appreciated») гасило бы требование.
    r'highly\s+valued|(?:is|are|will\s+be)\s+(?:highly\s+)?(?:valued|appreciated)|'
    r'(?:is|are|would\s+be|will\s+be)\s+an?\s+(?:strong\s+|real\s+)?asset)',
    re.IGNORECASE,
)

# Название языка, за которым идёт право/гражданство, — это юрисдикция, а не язык:
# «eligibility to work in accordance with PORTUGUESE LAW» (Concentrix, AI Labeling
# Agent (Russian-Speaking), Лиссабон, 13.10.2026) резалось как «нужен португальский».
_LANG_AS_JURISDICTION = re.compile(
    r'^\s+(?:labou?r\s+)?(?:law|laws|legislation|regulations?|citizens?|citizenship|'
    r'nationals?|nationality|residents?|residency|authorities|entity|subsidiary)\b',
    re.IGNORECASE)

# Языковые курсы в списке льгот — тоже не требование: «english, PORTUGUESE &
# spanish CLASSES; training budget» (ExoClick, Advertiser Account Manager (Russian
# Speaker), 13.10.2026). Язык может стоять в начале перечисления, курсы — в конце.
_LANG_AS_BENEFIT = re.compile(
    r'^(?:\s*(?:,|&|/|\band\b|\bor\b|\bи\b)\s*[a-zа-яё]+)*\s+(?:language\s+)?'
    r'(?:classes|courses|lessons|tuition|tutoring|курс\w*|урок\w*|заняти\w*)\b',
    re.IGNORECASE)
# Русский порядок слов обратный: «бесплатные КУРСЫ английского и испанского».
_LANG_BENEFIT_BEFORE = re.compile(
    r'(?:classes|courses|lessons|курс\w*|урок\w*|заняти\w*)\s+(?:по\s+)?'
    r'(?:[a-zа-яё]+\s*(?:,|&|\band\b|\bи\b)\s*)*$',
    re.IGNORECASE)

# Русский — один из вариантов: «German OR Russian», «Russian/Ukrainian»,
# «Mandarin, Cantonese or Russian». Кандидату русский родной, значит требование
# выполнено. Между нашим языком и русским допускается только перечисление языков.
_RU_WORD = r'(?:russian|русск\w*)'
# Запятая сама по себе — НЕ выбор: «fluent in Russian, German and English» требует
# все три. Альтернативу задают только «or», «или», «либо» и «/».
_RU_ALT_AFTER = re.compile(
    r'^(?:\s*,\s*[a-zа-яё]+)*\s*(?:/|\bor\b|\bили\b|\bлибо\b)\s*' + _RU_WORD,
    re.IGNORECASE)
_RU_ALT_BEFORE = re.compile(
    _RU_WORD + r'\s*(?:/|\bor\b|\bили\b|\bлибо\b)\s*$', re.IGNORECASE)
# Каталог «на выбор»: «our teams are proficient in either one or more of the
# following languages: english, french, …, russian, and many more» (Accenture,
# Inside Sales with Russian and English, 13.10.2026). Русский в каталоге — значит
# ни один из перечисленных языков не обязателен по отдельности.
_LANG_CATALOG = re.compile(
    r'(?:one\s+or\s+more|any|either|one)\s+of\s+the\s+following\s+languages', re.IGNORECASE)
_CLAUSE_BREAK = re.compile(r'[.;•\n]')


def _russian_alternative(blob: str, start: int, end: int) -> bool:
    """True, если упомянутый язык стоит в ряду альтернатив вместе с русским."""
    if _RU_ALT_AFTER.match(blob[end:end + 80]):
        return True
    if _RU_ALT_BEFORE.search(blob[max(0, start - 80):start]):
        return True
    left = blob[max(0, start - 300):start]
    breaks = list(_CLAUSE_BREAK.finditer(left))
    clause_start = breaks[-1].end() if breaks else 0
    right = blob[end:end + 300]
    rb = _CLAUSE_BREAK.search(right)
    clause = left[clause_start:] + blob[start:end] + (right[:rb.start()] if rb else right)
    return bool(_LANG_CATALOG.search(clause)) and bool(re.search(_RU_WORD, clause, re.I))


# Заголовки разделов. Язык, упомянутый в разделе «желательно», НЕ является
# требованием — окна ±60 символов для этого мало: заголовок раздела стоит выше,
# через несколько пунктов списка. Реальный случай 05.08.2026: Fireblocks
# «Technical Support Engineer, APAC» — Mandarin на позиции 2901, а заголовок
# «Preferred qualifications» на 2639 (за 262 символа) → гейт считал язык
# обязательным и резал подходящую вакансию.
_OPTIONAL_SECTION = re.compile(
    r'(preferred qualification|preferred skill|nice[\s-]to[\s-]have|'
    r'bonus point|good to have|desirable|advantageous|will be a plus|'
    # Двоеточие входит в пометку: по нему русский заголовок отличается от оговорки
    # (см. _marker_is_header).
    r'будет\s+(?:плюсом|преимуществом)(?:[ \t]*:)?|'
    r'(?:плюсом|преимуществом)\s+будет(?:[ \t]*:)?|'
    r'желательн|приветствуется|как преимущество)',
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
# Наречие между связкой и пометкой — та же оговорка: «…EMI companies is HIGHLY
# desirable.» (Advanced Payment Solutions, 18.10.2026) читалось как заголовок, и
# весь хвост, включая «Fluent English … is required», уходил в «желательно».
_LINKING_VERB_BEFORE = re.compile(
    r'\b(is|are|was|were|be|been|will|would|it.s|это)\s*(?:an?|the)?\s*(?:\w+ly\s*)?$',
    re.IGNORECASE)
# Пометка «плюс», с которой начинается заголовок следующего раздела.
_PLUS_STARTS_HEADER = re.compile(
    r'(?:preferred|bonus)\s+(?:qualification|skill|requirement|point)', re.IGNORECASE)

# Русские заголовки «Будет преимуществом» / «Будет плюсом» / «Плюсом будет» — самые
# частые на hh.ru, и они не признавались заголовком вообще: язык из раздела-плюшки
# становился требованием. Боевой случай 19.09.2026: ARBI Pay, Operations Manager,
# 58 баллов при пороге 42 — срезана гейтом за тайский под «Будет преимуществом».
# Та же фраза бывает оговоркой («опыт в поддержке будет плюсом, но не обязателен»),
# поэтому заголовком она считается, только если стоит отдельной строкой или с
# двоеточием. Замер по 42 вхождениям в батче 19.08: 26 заголовков именно такой
# формы, 11 оговорок — все посреди фразы со строчной буквы.
_RU_OPTIONAL_MARKER = re.compile(
    r'^(?:будет\s+(?:плюсом|преимуществом)|(?:плюсом|преимуществом)\s+будет)',
    re.IGNORECASE)
_LINE_START = re.compile(r'(?:^|\n)[ \t ]*$')


def _marker_is_header(before: str, marker: str) -> bool:
    """Пометка «желательно» — ЗАГОЛОВОК раздела, а не оговорка к текущему пункту?

    Различать обязательно, иначе оговорка гасит требования соседних пунктов, а
    заголовок наоборот делает обязательным то, что помечено как плюс.
    Границей предложения пользоваться НЕЛЬЗЯ: после снятия HTML списки часто идут
    без точек, и настоящий заголовок выглядит стоящим в середине фразы (Yodeck:
    «…at C1 level or above Nice to have Previous helpdesk…»).
    """
    m = marker.strip()
    if _RU_OPTIONAL_MARKER.match(m):
        return m.endswith(":") or bool(_LINE_START.search(before[-8:]))
    if not _HEADERISH_OPTIONAL.match(m):
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
    # требованию она не относится («…English, at C1 level or above. Nice to have …»).
    # «Preferred» — ещё и начало заголовка «Preferred Qualifications»: OKX P2P и
    # Acumatica (18.10.2026), «Fluent in English; Preferred Qualifications …» —
    # обязательный английский считался желательным.
    if plus and (_marker_is_header(tail[:plus.start()], plus.group(0))
                 or _PLUS_STARTS_HEADER.match(tail[plus.start():])):
        plus = None
    if plus:
        between = tail[:plus.start()]    # случай 2
        # «Fluent in English and any other language is a plus» (HRus, 18.10.2026):
        # «плюс» о ДРУГИХ языках. Проверять до продолжения списка — союз «and»
        # иначе приписывает пометку нашему языку. RONIN («Russian and/or Greek»)
        # не задевает: там язык назван поимённо, а не обобщённо.
        if _OTHER_LANG_GENERIC.search(between):
            return False
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
        if _LANG_AS_JURISDICTION.match(blob[m.end(): m.end() + 40]):
            continue
        if (_LANG_AS_BENEFIT.match(blob[m.end(): m.end() + 60])
                or _LANG_BENEFIT_BEFORE.search(blob[max(0, m.start() - 60): m.start()])):
            continue
        if _russian_alternative(blob, m.start(), m.end()):
            continue
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

# Стоп-лист работодателей (санкции, уголовные дела) — см. employer_blocklist в
# criteria.yaml. Целое слово, а не подстрока: «htx» не должен срезать «Chtx Labs».
_EMPLOYER_BLOCKLIST = [
    # Пробел в термине — любой пробельный: hh пишет «Meta\xa0Force» неразрывным.
    (term, re.compile(r"(?<!\w)" + re.escape(term.lower()).replace(r"\ ", r"\s+")
                      + r"(?!\w)"))
    for term in CRITERIA.get("employer_blocklist") or []
]
_BLOCKED_EMPLOYER_REASON = "работодатель в стоп-листе (санкции/уголовные дела)"


def _blocked_employer(company: str | None, title: str | None) -> str | None:
    """Термин стоп-листа, найденный в компании или заголовке, иначе None.

    Заголовок проверяется потому, что у Telegram-вакансий в поле `company` лежит
    канал, а работодатель упоминается в заголовке поста. Описание не проверяется:
    фраза «опыт работы с Garantex» говорит о кандидате, а не о работодателе.
    """
    hay = f"{_n(company)} | {_n(title)}"
    for term, rx in _EMPLOYER_BLOCKLIST:
        if rx.search(hay):
            return term
    return None

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
    # Отраслевой стоп-лист живёт в профиле, а не в criteria.yaml: без него в
    # отпечатке правка `industries_avoid` не переоткрывала прошлые отказы —
    # вакансии, отсеянные старым списком, не возвращались (ревизия 17.09.2026).
    avoid = "|".join(sorted(AVOID_KW))
    blob = criteria + source + avoid
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:12]


def _matches(term: str, text: str) -> bool:
    """Матч термина в тексте. Три режима:
    - `стем*` — префиксный матч от границы слова (`\\bстем`): ловит все падежные
      формы («операц*» → операции/операциям/операционный), но не середину слова
      («кооперация» не сматчит «операц*»). Нужен для русской морфологии.
    - одиночный токен без спецсимволов — слово целиком (`\\bслово\\b`).
    - фраза/со спецсимволами — подстрока.
    """
    if "*" in term[:-1]:
        # Звёздочка В СЕРЕДИНЕ («внедрени* ии») до 17.09.2026 искалась буквально
        # и не совпадала никогда — тихо мёртвый критерий. Каждая часть — стем.
        parts = [re.escape(p) for p in term.split("*")]
        return re.search(r"\b" + r"\w*".join(parts), text) is not None
    if term.endswith("*"):
        stem = term[:-1]
        return re.search(r"\b" + re.escape(stem), text) is not None
    if re.fullmatch(r"[a-zа-я0-9.\- ]+", term) and " " not in term and "." not in term:
        return re.search(r"\b" + re.escape(term) + r"\b", text) is not None
    return term in text


def _hits(terms: list[str], text: str) -> tuple[int, list[str]]:
    found = [t for t in terms if _matches(t, text)]
    return len(found), found


def _required_hits(terms: list[str], text: str) -> tuple[int, list[str]]:
    """Как _hits, но фраза засчитывается, только если хоть одно её вхождение —
    требование, а не пометка «плюс» и не раздел «желательно».

    Для штрафа english_penalty: до 18.10.2026 он считал фразы по всему тексту,
    хотя гейт английского те же разделы уже различал. xHuman, hh 138458820:
    «Разговорный английский язык» под «Будет плюсом:» → −14…−18 во всех ролях.
    """
    found = []
    for t in terms:
        if not _matches(t, text):
            continue
        spans = [(m.start(), m.end())
                 for m in re.finditer(re.escape(t.rstrip("*")), text)]
        # Терм со звёздочкой в середине буквально не ищется — штрафуем как раньше.
        if not spans or any(not _mention_is_optional(text, s, e)
                            and not _in_optional_section(text, s) for s, e in spans):
            found.append(t)
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


# ── Работа за счёт СВОИХ денег ────────────────────────────────────────────────
# `global_hard_exclude` в criteria.yaml умеет только дословные фразы (`_matches`
# понимает `*` лишь в КОНЦЕ термина). 26.08.2026 туда положили формулировки из
# тогдашней редакции вакансии «Специалист по межбиржевой торговле» — и 18.09.2026
# она пришла снова, потому что работодатель ПЕРЕПИСАЛ текст, сохранив смысл:
#   «первоначальный капитал»            → «тестовый капитал»
#   «с привлечением собственных средств» → «использование собственного
#                                          торгового капитала»
# Ни одна из 11 дословных фраз не совпала, предфильтр дал 78 баллов, AI — 82.
# Поэтому здесь regex по СЕМЕЙСТВУ формулировок, а не ещё один список фраз:
# следующая перефразировка не должна открывать ту же дверь.
#
# Замер на корпусе 4911 описаний (data/diag_batch.jsonl): 7 совпадений, все семь —
# эта же вакансия в шести каналах. Ложных ноль.
_OWN = r'(?:собственн\w*|свои\w*|свой|сво[её]|личн\w*)'
_MONEY = r'(?:капитал\w*|средств\w*|деньг\w*|депозит\w*)'
# Негативный хвост: «собственные СРЕДСТВА ЗАЩИТЫ информации» — это продукт
# компании, а не деньги кандидата. Зафиксировано замером 26.08.2026 на другом
# корпусе (1251 описание), где голое «собственные средства» дало такой промах.
_NOT_MONEY_TAIL = r'(?!\w*\s+(?:защит|связи|массов|производств|индивидуальн))'
_OWN_CAPITAL_REQUIRED = re.compile(
    _OWN + r'[\s,]+(?:\w+[\s,]+){0,2}?' + _MONEY + _NOT_MONEY_TAIL, re.UNICODE)
# Отдельная ветка: деньги «на обучение», которые потом станут своими.
_SEED_CAPITAL = re.compile(
    r'(?:тестов\w+|стартов\w+|первоначальн\w+|начальн\w+)[\s,]+капитал\w*', re.UNICODE)
#
# ⚠️ Намеренно НЕ добавлено: «вознаграждение/доход зависит от результата».
# На этом корпусе оно дало те же 7 совпадений и 0 ложных, но для роли
# `sales_remote` это нормальная формулировка комиссионной оплаты, и первая же
# живая вакансия с процентом от продаж была бы срезана. Риск больше пользы.


def _own_capital_required(blob: str) -> bool:
    """True, если работать предлагается на СВОИ деньги — перенос риска на
    кандидата. Это не работа, а торговля за свой счёт под видом найма."""
    return bool(_OWN_CAPITAL_REQUIRED.search(blob) or _SEED_CAPITAL.search(blob))


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

    if _own_capital_required(blob):
        return "работа за счёт собственного капитала кандидата"

    modality = _english_modality(blob)
    if modality in _ENGLISH_GATE_LEVELS:
        what = "разговорный" if modality == "spoken" else "свободный"
        return f"требуется {what} английский (у кандидата A1–A2)"

    if _night_shift_mode(blob) == "core":
        return "ночные смены как режим работы"

    rigid = _rigid_schedule(blob)
    if rigid:
        return f"сменный график как режим: {rigid}"

    city = _office_elsewhere(blob)
    if city:
        return f"офис/гибрид в другом городе ({city.title()}), удалёнки нет"

    cold = _cold_outreach(blob)
    if cold:
        return f"холодный поиск клиентов: «{cold}»"

    return None


# Русскоязычный контур заменяет домен в ролях поддержки и аккаунт/продаж — решение
# владельца 14.10.2026 по ресерчу LinkedIn. Русскоязычная поддержка и аккаунт-
# менеджмент за рубежом срезались правилом «нет финтеха»: S&P Global «Customer
# Success Manager (Fluent Russian)», ExoClick «Advertiser Account Manager (Russian
# Speaker)», «Russian Speaking Customer Service Representative».
# Только АНГЛИЙСКИЕ формулировки: на hh любая вакансия русскоязычная, а «СНГ/CIS»
# там означает географию продаж. Замер варианта с «СНГ/русскоязычн»: +5 мусора с hh
# («Менеджер по продажам СНГ — продукты питания», «ПЦР-оборудование»). С английскими
# формулировками на 3615 вакансиях hh/ATS/Telegram — +0, на 75 русскоязычных LinkedIn
# — +6, пропало 0.
_RU_DOMAIN_ROLES = frozenset({"support_fintech", "sales_remote"})
_RU_SPEAKING_EN = re.compile(
    r"russian[\s-]speak\w*|russian\s+speakers?|(?:fluent|native)\s+(?:in\s+)?russian|"
    r"russian[\s-]language\s+(?:support|desk|team|required)|russian\s+desk",
    re.IGNORECASE)


def passes_hard_gates(title: str, text: str, role_key: str) -> tuple[bool, list[str]]:
    """Дешёвый предфильтр. Возвращает (bool, [причины])."""
    role = CRITERIA["roles"][role_key]
    blob = f"{_n(title)} {_n(text)}"

    for t in CRITERIA["global_hard_exclude"]:
        if _matches(t, blob):
            return False, [f"hard-exclude: '{t}'"]

    # Только заголовок: фраза в описании не делает вакансию модерацией (см. criteria.yaml)
    for t in CRITERIA.get("global_title_exclude", []):
        if _matches(t, _n(title)):
            return False, [f"title-exclude: '{t}'"]
    # ...и внутри одной роли: «онлайн-школа» режет продажи курсов, но не
    # «Технический специалист GetCourse» в AI-автоматизации
    for t in role.get("title_exclude", []):
        if _matches(t, _n(title)):
            return False, [f"role-title-exclude: '{t}'"]

    extra_reason = _extra_hard_gates(title, text, role_key)
    if extra_reason:
        return False, [extra_reason]

    n_role, _ = _hits(role["must_role"], blob)
    if n_role == 0:
        return False, ["нет ролевых ключевых слов (не та функция)"]

    if role.get("must_domain"):
        n_dom, _ = _hits(role["must_domain"], blob)
        if n_dom == 0:
            if role_key in _RU_DOMAIN_ROLES and _RU_SPEAKING_EN.search(blob):
                return True, ["gate ok (русскоязычный контур вместо домена)"]
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
    loc_line = loc_match.group(1) if loc_match else ""
    foreign_office = False
    if loc_match and not _EXPLICIT_REMOTE.search(blob):
        if not _RU_LOCATION.search(loc_line) and not _hits(CRITERIA["relocation_ok"], loc_line)[0]:
            remote = False   # поле локации перевешивает «remote» из текста
            onsite = True
            foreign_office = True

    # Формат из ПОЛЯ hh главнее слов текста — так же, как workplace_type у ATS
    # (normalize.detect_remote). Строку кладёт hh_enrich, поэтому она есть у всех
    # дообогащённых вакансий, то есть у всех hh-вакансий, дошедших до отбора.
    # С 26.10.2026 офис/гибрид в ДРУГОМ городе с известной локацией режет гейт
    # `_office_elsewhere`. Сюда доходят: пустая локация и текст, явно обещающий
    # удалёнку, — им остаётся штраф `onsite_ru_city`; и Самара — `onsite_home`.
    ru_office = None
    hh_fmt = _HH_WORK_FORMAT.search(blob)
    if hh_fmt:
        formats = set(re.findall(r"[a-z_]+", hh_fmt.group(1)))
        if "remote" in formats:
            remote, onsite = True, False
        else:
            remote = False
            if not foreign_office and not _hits(CRITERIA["relocation_ok"], loc_line)[0]:
                onsite = False   # штраф ниже свой, не «офис за рубежом» −30
                ru_office = "home" if _HOME_CITY.search(loc_line) else "city"

    if remote:
        score += _W["remote"]; reasons.append("remote")
    if reloc:
        score += _W["relocation"]; reasons.append("страна релокации подходит")

    if ru_office == "city":
        score += _W.get("onsite_ru_city", -20)
        reasons.append("офис/гибрид в другом городе РФ, удалёнки нет")
    elif ru_office == "home":
        sub = _W.get("onsite_home", 0)
        score += sub
        reasons.append(f"{sub} офис/гибрид в Самаре" if sub else "офис/гибрид в Самаре")

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
    nep, fep = _required_hits(CRITERIA["english_penalty"], blob)
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

    # Сотрудник сам пользуется ИИ — по тексту описания, без заголовка (см. 3d)
    add = _W.get("ai_tools", 0)
    if add and ai_tools_phrase(text):
        score += add; reasons.append(f"+{add} ценят ИИ-инструменты")

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


_BRACKETS = re.compile(r"\(([^)]*)\)")
# Что в скобках НЕ делает вакансию другой: формат работы, тип занятости,
# гендерные пометки вакансий из ЕС. Регион в скобках — делает (см. dedupe_key).
_SERVICE_BRACKETS = {
    "remote", "удаленно", "удалённо", "hybrid", "гибрид", "onsite", "on site",
    "office", "офис", "full time", "fulltime", "part time", "parttime",
    "полная занятость", "частичная занятость", "контракт", "contract",
    "m f d", "f m d", "m w d", "all genders", "anywhere", "any location",
}


def _drop_service_brackets(m: re.Match) -> str:
    inner = re.sub(r"[^\w ]+", " ", m.group(1), flags=re.UNICODE)
    inner = re.sub(r"\s+", " ", inner).strip().lower()
    return " " if inner in _SERVICE_BRACKETS else f" {inner} "


def dedupe_key(company: str | None, title: str | None) -> str:
    """Ключ near-дубликата: одна и та же роль на разных бордах (или в нескольких
    локациях) приходит с РАЗНЫМИ id и проходит дедуп по id. В боевом прогоне
    25.07 так пришли «Professional Services Consultant @ Ripple» ×3 и
    «Risk & Monitoring Analyst IV @ Coinbase» ×2 (HEALTH_AUDIT M2).

    Нормализуем регистр, пунктуацию и пробелы. Из скобок выбрасываем ТОЛЬКО
    служебные хвосты («(Remote)», «(Full-time)»): содержательные скобки остаются,
    иначе разные вакансии склеиваются в одну и вторая теряется навсегда — до
    17.09.2026 «Менеджер P2P (LatAm)» и «Менеджер P2P (CIS — СНГ)» давали один
    ключ, и вторая помечалась увиденной.
    """
    text = f"{_n(company)}|{_n(title)}"
    text = _BRACKETS.sub(_drop_service_brackets, text)
    text = re.sub(r"[^\w|]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


# Шапка Telegram-поста — украшение КАНАЛА, а не текст вакансии: цена цифрами,
# хэштеги, эмодзи. 18.09.2026 одна вакансия пришла двумя карточками, потому что
# @workingincrypto приписал в начало «2474 »: пять символов сдвинули окно в 300
# символов, и md5-отпечаток разошёлся (c966353e против 6503df32 у двух других
# каналов, которые схлопнулись правильно). Срезаем ведущие числа/хэштеги/символы,
# чтобы отпечаток считался от первых слов САМОГО объявления.
_TG_DECOR_HEAD = re.compile(r'^(?:\s*(?:#[^\s#]+|[\d.,+\-]+|[^\w\s#]))+', re.UNICODE)


def _tg_body(text: str) -> str:
    """Нормализованный текст поста без ведущих украшений канала."""
    return _TG_DECOR_HEAD.sub("", _n(text)).lstrip()


def job_dedupe_key(job) -> str:
    """Ключ near-дубликата для вакансии целиком. Один и тот же для дедупа внутри
    прогона (`split_duplicates`) и для постоянной таблицы `dedupe_keys`: если
    формулы разойдутся, межпрогонный дедуп молча перестанет узнавать копии."""
    company = getattr(job, "company", "") or ""
    # У вакансий из Telegram в поле `company` лежит НЕ работодатель, а канал,
    # который перепостил объявление (`telegram_parser` кладёт туда `@<канал>`).
    # Одна и та же вакансия в двух каналах давала два разных ключа и доходила
    # дважды — боевой случай 26.08.2026: «Требуется специалист по межбиржевой
    # торговле» пришла из @cryptovakansii и @opento_crypto с одинаковым текстом
    # и одинаковым баллом 90. Для дедупа канал — шум, дедуплицируем по заголовку.
    # ...но тогда по одному заголовку склеиваются РАЗНЫЕ вакансии: у постов
    # заголовок часто общий («Требуется менеджер»), а текст разный. Поэтому
    # для Telegram к ключу добавляем отпечаток начала описания — репост того
    # же текста в другом канале даёт тот же отпечаток и по-прежнему схлопнется,
    # а два разных объявления — нет.
    extra = ""
    if str(getattr(job, "source", "")).startswith("telegram"):
        company = ""
        head = _tg_body(getattr(job, "description", "") or "")[:300]
        extra = "|" + hashlib.md5(head.encode("utf-8")).hexdigest()[:8]
    return dedupe_key(company, getattr(job, "title", "")) + extra


def split_duplicates(items: list) -> tuple[list, list]:
    """Делит на представителей и копии, сохраняя порядок (побеждает первый).
    Вход: Job или (Job, MatchResult). Копии — парами (копия, её представитель):
    планировщику нужен id представителя, чтобы пометить копию финально."""
    first: dict[str, object] = {}
    kept, dropped = [], []
    for item in items:
        job = item[0] if isinstance(item, tuple) else item
        key = job_dedupe_key(job)
        if key in first:
            dropped.append((item, first[key]))
            continue
        first[key] = item
        kept.append(item)
    return kept, dropped


def dedupe_jobs(pairs: list) -> list:
    """Оставляет по одному представителю на near-дубликат, сохраняя порядок.
    Вход: [(Job, MatchResult)] — как в scheduler перед отправкой."""
    return split_duplicates(pairs)[0]


# ── Направление 27.10.2026: продажи без ИИ-продукта и зарплата ниже пола ──────────
# Цель владельца 26.10 — рост в ИИ и доход выше пола (criteria.yaml, direction_gate).
# Продажи остаются, если ИИ или SaaS — продукт или сфера компании. Замер на 178
# продажных карточках hh: «используете ИИ-инструменты», «опыт в SaaS будет плюсом»,
# «внедряем AI» стояли у сантехники на Ozon, недвижимости, реагентов — это не продукт.
_SALES_TITLE = re.compile(
    r"продаж|\bsales\b|телемаркет|лидоген|business develop|бизнес-?девелоп|\bbd[mr]?\b"
    r"|account executive|менеджер по привлечению")
# «Менеджер по работе с клиентами (без продаж)», Sales Support — не продавец
_NOT_SALES_TITLE = re.compile(
    r"без продаж|не продаж|no sales|sales (?:support|operations|ops|coordinator|assistant)"
    r"|поддержк\w* продаж|ассистент\w* (?:отдела )?продаж")
# Продажа обучения — онлайн-школа, даже если курс про нейросети (решение 18.10)
_COURSE_TITLE = re.compile(r"обучени|\bкурс|урок|образовани|диагностик")
_AI_SAAS = re.compile(
    r"искусственн\w* интеллект|нейросет|нейронн\w* сет|\bllm|chatgpt|\bgpt|claude|\bии\b|\bai\b"
    r"|генеративн|машинн\w* обучени|machine learning|\bsaas\b|\biaas\b|\bpaas\b|\bcloud\b"
    r"|облачн\w* (?:сервис|платформ|решени|продукт|по\b)"
    # Собственный IT-продукт — запасное направление владельца (CSM/внедрение в IT-продуктах).
    # Без этого срезались Minervasoft, YCLIENTS, Sellmonitor, Loyal Club: «SaaS» у них
    # только в требованиях, а продукт назван «IT-сервис», «платформа», «наше ПО».
    r"|\b(?:it|ит)[- ]?(?:продукт|сервис|решени|компани)|программн\w* продукт|\bsoftware\b"
    r"|разработчик\w* (?:собственн|программ|решени|платформ|сервис|продукт)"
    r"|разрабатываем (?:решени|продукт|платформ|сервис)|собственн\w* (?:платформ|сервис|продукт)"
    r"|сервис\w* (?:аналитики|по подписанию)"
    r"|платформ\w* для (?:бизнес|малого|компани|мониторинг|автоматизац)")
# Слева в той же фразе — требование к кандидату или его инструмент, а не продукт.
# «Аккредитованная IT-компания» — налоговый статус: так пишут телеком и банки.
_AI_INCIDENTAL_BEFORE = re.compile(
    r"опыт|experience|желательн|умени|умеешь|умеете|навык|знани|владе|использ|применя"
    r"|работ\w* (?:с|в)\b|с помощью|внедряем|готовност|обучени|курс|аккредитованн")
_AI_INCIDENTAL_AFTER = re.compile(r"^[\s-]*(?:инструмент|tools?\b|помощник|ассистент)")
# SaaS-CRM — продукт только в заголовке («Менеджер по продажам amoCRM»); в описании
# продаж это рабочий инструмент («ведение клиентов в Битрикс24») — замер 27.10
# вернул по нему электрощитовое оборудование, франшизы и интернет-магазин.
_SAAS_BRAND_TITLE = re.compile(r"amocrm|битрикс ?24|bitrix ?24")
# Переменная часть дохода: при ней «до X» в поле hh — оклад, а не доход
_VARIABLE_PAY = re.compile(
    r"преми|бонус|\bkpi\b|\bкпи\b|процент\w* от|% от|\bот \d+ ?%|комисси\w* от|сдельн"
    r"|переменн\w* част|систем\w* мотивац|мотивационн|не ограничен|без потолка|13-?(?:я|й) зарплат")
_DIRECTION = CRITERIA.get("direction_gate", {})


def _ai_product_mentioned(text: str) -> bool:
    """True, если ИИ/SaaS в тексте — продукт или сфера, а не навык и не инструмент."""
    for m in _AI_SAAS.finditer(text):
        before = re.split(r"[.!?;\n•]", text[max(0, m.start() - 70): m.start()])[-1]
        if _AI_INCIDENTAL_BEFORE.search(before) or _AI_INCIDENTAL_AFTER.search(text[m.end():]):
            continue
        return True
    return False


def _sales_without_ai_product(title: str, text: str) -> str | None:
    t = _n(title)
    if not _SALES_TITLE.search(t) or _NOT_SALES_TITLE.search(t):
        return None
    if _COURSE_TITLE.search(t):
        return "продажа курсов/обучения"
    if _AI_SAAS.search(t) or _SAAS_BRAND_TITLE.search(t) or _ai_product_mentioned(_n(text)):
        return None
    return "продажи без ИИ/SaaS/IT-продукта"


def _salary_below_floor(job: Job) -> str | None:
    floor = _DIRECTION.get("salary_floor_net_rub")
    if not floor or job.salary_currency != "RUB" or not job.salary_max:
        return None   # «от X» без верха не режем: оклад ≠ доход
    if job.raw.get("salary_mode") not in (None, "MONTH"):
        return None   # за смену / за час
    top = job.salary_max * 0.87 if job.raw.get("salary_gross") else job.salary_max
    if top >= floor or _VARIABLE_PAY.search(_n(job.description)):
        return None
    return f"верх зарплаты {int(top) // 1000}k на руки ниже {floor // 1000}k, премий нет"


def direction_gate(job: Job) -> str | None:
    """Причина отсева по направлению владельца или None. Для hh — только по полной
    странице: в RSS-выжимке нет ни продукта компании, ни премий, и отказ по ней
    был бы окончательным (такие отказы не дообогащаются)."""
    if (job.source or "") == "hh.ru" and not job.raw.get("hh_full"):
        return None
    if _DIRECTION.get("sales_need_ai_product"):
        reason = _sales_without_ai_product(job.title, job.description)
        if reason:
            return reason
    return _salary_below_floor(job)


def score_job(job: Job) -> dict:
    """Скорит вакансию по всем ролям из criteria.yaml, возвращает лучшую по баллу.

    Локация подмешивается в текст: она хранится ОТДЕЛЬНЫМ полем, и раньше скоринг
    её не видел вообще. Из-за этого офисные вакансии в неподходящих странах
    (реальный случай 05.08.2026: Fireblocks «Technical Support Engineer, APAC»,
    location=Singapore) не получали штрафа — в описании страна не упоминалась.
    """
    blocked = _blocked_employer(job.company, job.title)
    if blocked:
        # Жёсткий отказ по всем ролям сразу: дообогащение описанием его не
        # переворачивает, поэтому причина НЕ входит в _SOFT_GATE_REASONS.
        results = [{"role": r, "passed_gate": False, "score": 0, "recommend": False,
                    "reasons": [f"{_BLOCKED_EMPLOYER_REASON}: {blocked}"]}
                   for r in CRITERIA["roles"]]
        return {"best": results[0], "all": results}

    direction = direction_gate(job)
    if direction:
        results = [{"role": r, "passed_gate": False, "score": 0, "recommend": False,
                    "reasons": [direction]} for r in CRITERIA["roles"]]
        return {"best": results[0], "all": results}

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


# Отказы, которые МОГУТ перевернуться, если дотянуть полное описание: гейт не нашёл
# ролевых или доменных слов. На источниках с обрезанным описанием (HH RSS отдаёт
# медианно 126 символов) такой отказ выносится фактически по заголовку.
# Остальные отказы — английский, опыт, гражданство, dev-роль, ночные смены —
# полным описанием не переворачиваются: в нём они только подтверждаются.
_SOFT_GATE_REASONS = frozenset({
    "нет ролевых ключевых слов (не та функция)",
    "нет крипто/web3 контекста (не та область)",
})


def is_soft_reject(scored: dict) -> bool:
    """True, если вакансия НЕ прошла отбор и ни одна роль не упёрлась в жёсткий блокер.

    На вход — готовый результат `score_job` (а не Job), чтобы не пересчитывать
    семь ролей второй раз: в планировщике он уже посчитан.

    Сюда попадают два случая, и оба лечатся полным описанием:
    - гейт не пустил из-за ролевых/доменных слов;
    - гейт пустил, но балл не дотянул до порога (боостов не из чего набрать).
    """
    best = scored["best"]
    if best["passed_gate"] and best["recommend"]:
        return False
    return all(
        r["passed_gate"] or r["reasons"][0] in _SOFT_GATE_REASONS
        for r in scored["all"]
    )
