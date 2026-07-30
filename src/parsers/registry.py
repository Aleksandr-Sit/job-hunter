"""Единый билдер парсеров — один источник правды о составе источников.

Раньше список был захардкожен ДВАЖДЫ: в `scheduler._build_parsers` и в
`tools/diag/dump_batch.build_parsers`. Списки разъехались (Habr был только в
первом), из-за чего все диагностические замеры воронки молча недосчитывали
источник (HEALTH_AUDIT F11). Теперь оба зовут этот модуль.
"""
from __future__ import annotations


def build_parsers(cfg: dict) -> list:
    """Собирает включённые в конфиге парсеры. Импорты внутри — чтобы падение
    одного модуля не роняло весь список на этапе импорта."""
    from .hh_parser import HHParser
    from .telegram_parser import TelegramParser
    from .web.ashby import AshbyParser
    from .web.contra import ContraParser
    from .web.cryptojoblist import CryptoJobListParser
    from .web.greenhouse import GreenhouseParser
    from .web.habr import HabrCareerParser
    from .web.laborx import LaborXParser
    from .web.lever import LeverParser
    from .web.linkedin import LinkedInParser
    from .web.remote3 import Remote3Parser
    from .web.remoteok import RemoteOKParser
    from .web.web3career import Web3CareerParser
    from .web.wellfound import WellFoundParser

    # (ключ в settings.yaml, класс, включён по умолчанию)
    spec = [
        ("hh", HHParser, True),
        ("remoteok", RemoteOKParser, True),
        ("cryptojoblist", CryptoJobListParser, True),
        ("web3career", Web3CareerParser, True),
        ("laborx", LaborXParser, True),
        ("remote3", Remote3Parser, True),
        ("wellfound", WellFoundParser, False),
        ("contra", ContraParser, False),
        ("ashby", AshbyParser, True),
        ("greenhouse", GreenhouseParser, True),
        ("lever", LeverParser, True),
        ("linkedin", LinkedInParser, False),
        ("habr", HabrCareerParser, True),
        ("telegram", TelegramParser, True),
    ]

    parsers_cfg = cfg.get("parsers", {})
    return [
        cls() for key, cls, default in spec
        if parsers_cfg.get(key, {}).get("enabled", default)
    ]
