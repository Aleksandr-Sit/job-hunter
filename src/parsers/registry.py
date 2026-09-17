"""Единый билдер парсеров — один источник правды о составе источников.

Раньше список был захардкожен ДВАЖДЫ: в `scheduler._build_parsers` и в
`tools/diag/dump_batch.build_parsers`. Списки разъехались (Habr был только в
первом), из-за чего все диагностические замеры воронки молча недосчитывали
источник (HEALTH_AUDIT F11). Теперь оба зовут этот модуль.
"""
from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

# (ключ в settings.yaml, модуль, класс, включён по умолчанию)
_SPEC = [
    ("hh", ".hh_parser", "HHParser", True),
    ("remoteok", ".web.remoteok", "RemoteOKParser", True),
    ("cryptojoblist", ".web.cryptojoblist", "CryptoJobListParser", True),
    ("web3career", ".web.web3career", "Web3CareerParser", True),
    ("laborx", ".web.laborx", "LaborXParser", True),
    ("remote3", ".web.remote3", "Remote3Parser", True),
    ("wellfound", ".web.wellfound", "WellFoundParser", False),
    ("contra", ".web.contra", "ContraParser", False),
    ("ashby", ".web.ashby", "AshbyParser", True),
    ("greenhouse", ".web.greenhouse", "GreenhouseParser", True),
    ("lever", ".web.lever", "LeverParser", True),
    ("linkedin", ".web.linkedin", "LinkedInParser", False),
    ("habr", ".web.habr", "HabrCareerParser", True),
    ("telegram", ".telegram_parser", "TelegramParser", True),
]


def build_parsers(cfg: dict) -> list:
    """Собирает включённые в конфиге парсеры.

    Импорт и создание — ПО ОДНОМУ и под защитой. До 17.09.2026 все импорты стояли
    одним блоком в начале функции: сломанная зависимость или опечатка в одном
    парсере роняла весь прогон, хотя докстрока обещала ровно обратное. Конструктор
    защищён тоже — часть парсеров читает `settings.yaml` прямо в `__init__`.
    """
    parsers_cfg = cfg.get("parsers", {})
    out = []
    for key, module, cls_name, default in _SPEC:
        if not parsers_cfg.get(key, {}).get("enabled", default):
            continue
        try:
            cls = getattr(importlib.import_module(module, __package__), cls_name)
            out.append(cls())
        except Exception as e:
            logger.error("Парсер %s не поднялся (%s: %s) — прогон идёт без него",
                         key, type(e).__name__, str(e)[:150])
    return out
