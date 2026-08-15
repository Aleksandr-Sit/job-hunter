"""Секреты не должны доезжать до строки лога.

Поводом стал реальный инцидент 15.08.2026: httpx на INFO печатал URL целиком,
а токен Telegram лежит внутри URL. Здесь зафиксированы формы, которые обязаны
вырезаться, и — не менее важно — обычный текст, который трогать нельзя.
"""
import logging

from src.log_redact import RedactingFilter, redact


class TestRedact:
    def test_telegram_token_in_url(self):
        # Ровно та строка, которая писалась в лог 8600 раз в сутки.
        s = redact("HTTP Request: POST https://api.telegram.org/bot123456789:"
                   "AAGCMTF4rSPQM5jjvbSHDyUbnY1E3ZOII/getUpdates \"200 OK\"")
        assert "AAGCMTF4" not in s
        assert "REDACTED" in s
        assert "getUpdates" in s          # полезная часть строки сохраняется

    def test_query_parameters(self):
        for raw, secret in [
            ("https://x.io/v1?api_key=abcdef123456&page=2", "abcdef123456"),
            ("https://x.io/v1?token=zzzsecretzzz", "zzzsecretzzz"),
            ("POST /auth password=hunter2 ok", "hunter2"),
        ]:
            out = redact(raw)
            assert secret not in out, raw
            assert "REDACTED" in out

    def test_query_secret_keeps_following_params(self):
        out = redact("https://x.io/v1?api_key=abcdef123456&page=2")
        assert "page=2" in out           # маска не должна съедать хвост

    def test_bearer_header(self):
        out = redact("Authorization: Bearer abcdefghij1234567890XYZ")
        assert "abcdefghij1234567890XYZ" not in out

    def test_prefixed_keys(self):
        for key in ("csk-abcdefghij1234567890",
                    "sk-abcdefghij1234567890",
                    "ghp-abcdefghij1234567890"):
            assert key not in redact(f"ключ {key} в тексте")

    def test_ordinary_text_untouched(self):
        # Перелов дороже недолова: замусоренный лог невозможно читать.
        for s in ("Greenhouse Coinbase: 167 jobs",
                  "After dedup: 335 unseen | After pre-filter: 9 to AI",
                  "LinkedIn Кипр: +54 уникальных",
                  "Matched 3 jobs above threshold 55%"):
            assert redact(s) == s

    def test_empty_and_none_safe(self):
        assert redact("") == ""
        assert redact(None) is None


class TestRedactingFilter:
    def _record(self, msg, args=None):
        return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)

    def test_cleans_message(self):
        r = self._record("GET /bot999999999:AAGabcdefghij1234567890/sendMessage")
        RedactingFilter().filter(r)
        assert "AAGabcdefghij" not in r.getMessage()

    def test_cleans_args_not_just_msg(self):
        # logger.info("HTTP %s", url) кладёт URL в args — чистка одного msg его пропустит.
        r = self._record("HTTP %s", ("https://api.telegram.org/bot777777777:"
                                     "AAGsecretsecretsecret12/getUpdates",))
        RedactingFilter().filter(r)
        assert "AAGsecretsecret" not in r.getMessage()

    def test_filter_always_passes_record(self):
        r = self._record("обычная строка")
        assert RedactingFilter().filter(r) is True
