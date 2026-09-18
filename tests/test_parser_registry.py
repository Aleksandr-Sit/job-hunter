"""Сломанный парсер не должен ронять прогон целиком.

Ревизия 17.09.2026: докстрока `build_parsers` обещала «падение одного модуля не
роняет список», но все импорты стояли одним блоком в начале функции — то есть
ошибка в одном парсере убивала ВСЕ источники сразу.
"""
from src.parsers import registry


def _cfg(**enabled):
    return {"parsers": {k: {"enabled": v} for k, v in enabled.items()}}


class TestBuildParsers:
    def test_broken_module_does_not_kill_the_rest(self, monkeypatch, caplog):
        monkeypatch.setattr(registry, "_SPEC", [
            ("broken", ".web.no_such_module", "Nope", True),
            ("hh", ".hh_parser", "HHParser", True),
        ])
        parsers = registry.build_parsers(_cfg(broken=True, hh=True))
        assert [p.name for p in parsers] == ["hh"]
        assert "broken" in caplog.text

    def test_broken_constructor_does_not_kill_the_rest(self, monkeypatch, caplog):
        class Boom:
            def __init__(self):
                raise RuntimeError("нет секции в settings.yaml")

        monkeypatch.setattr(registry.importlib, "import_module",
                            lambda *a, **k: type("M", (), {"Boom": Boom, "HHParser": _Fake}))
        monkeypatch.setattr(registry, "_SPEC", [
            ("boom", ".x", "Boom", True),
            ("ok", ".y", "HHParser", True),
        ])
        parsers = registry.build_parsers(_cfg(boom=True, ok=True))
        assert len(parsers) == 1
        assert "boom" in caplog.text

    def test_disabled_parser_is_not_imported(self, monkeypatch):
        calls = []
        monkeypatch.setattr(registry.importlib, "import_module",
                            lambda m, p=None: calls.append(m) or type("M", (), {"X": _Fake}))
        monkeypatch.setattr(registry, "_SPEC", [("off", ".x", "X", True)])
        assert registry.build_parsers(_cfg(off=False)) == []
        assert calls == []

    def test_real_spec_matches_real_modules(self):
        """Ключи и классы в _SPEC должны существовать: опечатка теперь не падает
        громко, а тихо выключает источник — поэтому проверяем тестом."""
        parsers = registry.build_parsers({"parsers": {}})
        names = {p.name for p in parsers}
        assert {"hh", "greenhouse", "lever", "ashby", "telegram"} <= names
        # по умолчанию выключенные не поднимаются
        assert "wellfound" not in names


class _Fake:
    name = "fake"

    def __init__(self):
        pass
