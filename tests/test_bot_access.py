"""Бот — личный: чужой чат не получает ни списка откликов, ни кнопок.

Ревизия 17.09.2026: фильтра по chat_id не было вообще. Любой, кто узнает имя
бота, мог вызвать /applications (список вакансий, куда откликался владелец) и
жать кнопку «Письмо», тратя квоту AI.

Здесь же — разбор заголовка карточки: `lstrip("🎯 ")` срезал не тот символ, и в
трекер уходил заголовок с эмодзи балла (все 10 записей `applications`).
"""
import asyncio
from types import SimpleNamespace

import pytest

from src.bot.callback_handler import _parse_card, owner_only
from src.bot.formatter import _ROLE_LABELS


class _Query:
    def __init__(self):
        self.answers = []

    async def answer(self, text=""):
        self.answers.append(text)


def _update(chat_id, query=None):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id),
                           callback_query=query)


def _run(guard, update):
    return asyncio.run(guard(update, None))


class TestOwnerOnly:
    @pytest.fixture()
    def handler(self):
        calls = []

        async def inner(update, context):
            calls.append(update.effective_chat.id)
            return "готово"
        inner.calls = calls
        return inner

    def test_owner_passes(self, handler, monkeypatch):
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "555")
        assert _run(owner_only(handler), _update(555)) == "готово"
        assert handler.calls == [555]

    def test_stranger_blocked(self, handler, monkeypatch):
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "555")
        q = _Query()
        assert _run(owner_only(handler), _update(999, q)) is None
        assert handler.calls == [], "хендлер не должен был выполниться"
        assert q.answers == ["Этот бот личный"]

    def test_chat_id_compared_as_text(self, handler, monkeypatch):
        # В .env строка, в Telegram int — сравнение типов роняло бы фильтр молча.
        monkeypatch.setenv("TELEGRAM_CHAT_ID", " 555 ")
        assert _run(owner_only(handler), _update(555)) == "готово"

    def test_without_env_everyone_passes(self, handler, monkeypatch):
        # Обратная совместимость: без переменной бот не должен онеметь.
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        assert _run(owner_only(handler), _update(1)) == "готово"

    def test_no_chat_is_blocked(self, handler, monkeypatch):
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "555")
        upd = SimpleNamespace(effective_chat=None, callback_query=None)
        assert _run(owner_only(handler), upd) is None
        assert handler.calls == []


class TestParseCard:
    @pytest.mark.parametrize("emoji", ["🔥", "⭐", "✅", "👀", "🎯"])
    def test_any_score_emoji_is_stripped(self, emoji):
        title, company, score = _parse_card(
            f"{emoji} Специалист поддержки\nBitbanker · Remote\n\n78/100")
        assert title == "Специалист поддержки"
        assert company == "Bitbanker"
        assert score == 78

    def test_cyrillic_and_brackets_survive(self):
        title, _, _ = _parse_card("🔥 (Senior) Аналитик\nACME")
        assert title == "(Senior) Аналитик"

    def test_empty_card(self):
        assert _parse_card("") == ("", "", None)


class TestRoleLabels:
    def test_every_role_from_criteria_has_a_label(self):
        from src.matcher.pre_filter import CRITERIA
        missing = [r for r in CRITERIA["roles"] if r not in _ROLE_LABELS]
        assert not missing, f"роли без подписи показываются сырым ключом: {missing}"
