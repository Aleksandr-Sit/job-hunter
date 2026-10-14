"""Сортировка карточки вакансии по заголовку ДО скачивания описания.

Зачем. У LinkedIn описание — отдельный запрос, а карточка поиска несёт только
заголовок, компанию и город. Прогон 14.10.2026: ~1700 карточек, за 45 минут
стадии успели 19 описаний (все — Chainalysis из watch-списка, инженеры и HR),
остальные карточки выброшены. Решаем по заголовку, на что тратить запросы.

Отдельный модуль, а не функция в pre_filter.py: версия предфильтра — хеш его
исходника, и любая строка там заставила бы бота пересмотреть все прошлые отказы.
"""
from .pre_filter import (CRITERIA, _RU_MENTION, _SOFT_GATE_REASONS,
                         _blocked_employer, _hits, _n, passes_hard_gates)


def title_triage(title: str, company: str | None = None) -> str:
    """"drop" | "high" | "low".

    - drop — заголовок сам даёт ЖЁСТКИЙ отказ по всем ролям (dev, executive,
      чужой язык, ночные смены, работодатель из стоп-листа). Описание его не
      перевернёт: заголовок входит в текст скоринга, и гейт сработает снова.
    - high — в заголовке ролевое ИЛИ доменное слово роли, которую заголовок не
      режет, либо упомянут русский/СНГ.
    - low — по заголовку не понять (HR, Marketing, Data Analyst…): качаем после
      high, если хватит времени. Не выбрасываем.

    Замер на 18013 li_* из базы бота (21.06–14.10.2026):
      drop 2897 — до Telegram дошла 1 («Treasury Operator - night shift»);
      high 6831 — в Telegram 552 (8,1%);  low 8285 — в Telegram 143 (1,7%).
    Без домена и русского в high в low уходило 219 отправленных — «Crypto
    Specialist», «Anti-Money Laundering Analyst», «CS Specialist (Russian)».

    Своих списков слов нет — только гейты, must_role/must_domain из criteria.yaml
    и _RU_MENTION, чтобы не заводить второй источник правды об отборе.
    """
    if _blocked_employer(company, title):
        return "drop"
    t = _n(title)
    any_alive = False
    for role_key, role in CRITERIA["roles"].items():
        ok, reasons = passes_hard_gates(title, "", role_key)
        if not ok and reasons[0] not in _SOFT_GATE_REASONS:
            continue  # жёсткий отказ по этой роли
        any_alive = True
        if _hits(role["must_role"], t)[0] or _hits(role.get("must_domain") or [], t)[0]:
            return "high"
    if not any_alive:
        return "drop"
    return "high" if _RU_MENTION.search(t) else "low"
