"""Доступ к чатам hh через внутренний API chatik.hh.ru с сессией владельца.

Почему не публичный API hh: с 2026 года он не отдаёт соискателю отклики и
переписку. Остаётся сервис чатов chatik.hh.ru, который читает и сам сайт, — ему
нужна сессия (cookie) вошедшего пользователя.

Cookie экспортирует владелец из своего браузера и кладёт в папку вне git
(`config/hh_session/`, на сервер — scp файл→файл). Значения cookie здесь никогда
не печатаются и не пишутся в лог — только имена и число.

Без входа chatik отвечает 403 с JSON (проверено с VPS 26.10.2026). 401/403,
редирект на вход или HTML вместо JSON = «сессия истекла»; таймаут и 5xx — сбой
сети, а не сессии: у них разные алерты.
"""
import json
import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

API = "https://chatik.hh.ru/chatik/api/"

# UA обычного Chrome под Windows: сессия создана в браузере владельца, и запросы
# с «python-requests» выглядели бы подозрительно. Переопределяется
# HH_CHAT_USER_AGENT — можно вписать UA своего браузера.
_DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
_TIMEOUT = 20
_PAUSE = 0.3          # между запросами: не долбить сервис чатов
_MAX_PAGES = 50       # предохранитель постраничного обхода (по 20 чатов)


class SessionExpired(Exception):
    """hh не пускает: сессия истекла, cookie нет или они испорчены."""


class ChatikUnavailable(Exception):
    """chatik не ответил по-человечески: таймаут, 5xx, неожиданный формат."""


# ── cookie ────────────────────────────────────────────────────────────────────

def _is_hh(domain: str) -> bool:
    d = (domain or "").lstrip(".").lower()
    return d == "hh.ru" or d.endswith(".hh.ru")


def _parse_netscape(text: str) -> list[dict]:
    """Формат cookies.txt (его пишут расширения вроде «Get cookies.txt LOCALLY»).
    Строки `#HttpOnly_<домен>` — не комментарий, а cookie с флагом HttpOnly."""
    out = []
    for line in text.splitlines():
        line = line.strip("\r\n")
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _sub, path, secure, expires, name = parts[:6]
        value = "\t".join(parts[6:])
        try:
            exp = int(float(expires))
        except ValueError:
            exp = 0
        out.append({"domain": domain, "path": path or "/", "name": name,
                    "value": value, "secure": secure.upper() == "TRUE",
                    "expires": exp or None})
    return out


def _parse_json(text: str) -> list[dict]:
    """JSON-экспорт (Cookie-Editor и подобные): список объектов или {"cookies": [...]}."""
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("cookies", [])
    out = []
    for c in data if isinstance(data, list) else []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        exp = c.get("expirationDate") or c.get("expires") or 0
        try:
            exp = int(float(exp))
        except (TypeError, ValueError):
            exp = 0
        out.append({"domain": c.get("domain") or ".hh.ru", "path": c.get("path") or "/",
                    "name": c["name"], "value": str(c.get("value", "")),
                    "secure": bool(c.get("secure")), "expires": exp if exp > 0 else None})
    return out


def _parse_header(text: str) -> list[dict]:
    """Строка заголовка `Cookie: a=1; b=2` из DevTools → cookie домена .hh.ru."""
    body = text.strip()
    if body.lower().startswith("cookie:"):
        body = body[len("cookie:"):]
    out = []
    for part in body.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        if name:
            out.append({"domain": ".hh.ru", "path": "/", "name": name.strip(),
                        "value": value.strip(), "secure": True, "expires": None})
    return out


def parse_cookies(text: str) -> list[dict]:
    """Разбирает любой из трёх форматов. Возвращает только cookie доменов hh.ru."""
    stripped = text.lstrip("﻿").strip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        cookies = _parse_json(stripped)
    elif "\t" in stripped:
        cookies = _parse_netscape(stripped)
    else:
        cookies = _parse_header(stripped)
    return [c for c in cookies if _is_hh(c["domain"])]


def _cookie_files(path: Path) -> list[Path]:
    if path.is_dir():
        files = [p for p in path.iterdir()
                 if p.is_file() and p.suffix.lower() in (".txt", ".json")]
        # Свежий экспорт — последним: при совпадении имени он перекрывает старый.
        return sorted(files, key=lambda p: p.stat().st_mtime)
    return [path] if path.is_file() else []


def load_cookies(path: Path) -> list[dict]:
    """cookie hh из файла или из всех *.txt / *.json папки. Нет файлов → SessionExpired."""
    files = _cookie_files(Path(path))
    if not files:
        raise SessionExpired(f"нет файла cookie ({path})")
    merged: dict[tuple, dict] = {}
    for f in files:
        try:
            for c in parse_cookies(f.read_text(encoding="utf-8", errors="replace")):
                merged[(c["domain"].lstrip("."), c["name"], c["path"])] = c
        except (OSError, ValueError) as e:
            logger.warning("Чаты hh: файл cookie %s не прочитан (%s)", f.name, type(e).__name__)
    cookies = list(merged.values())
    now = time.time()
    alive = [c for c in cookies if not c["expires"] or c["expires"] > now]
    if not alive:
        raise SessionExpired("в файле cookie нет действующих cookie hh.ru")
    expired = len(cookies) - len(alive)
    logger.info("Чаты hh: cookie загружены — %d шт. из %d файл(ов)%s; имена: %s",
                len(alive), len(files),
                f", просрочено {expired}" if expired else "",
                ", ".join(sorted({c["name"] for c in alive})))
    return alive


def build_session(cookies: list[dict]) -> requests.Session:
    s = requests.Session()
    for c in cookies:
        s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"],
                      secure=c["secure"], expires=c["expires"])
    s.headers.update({
        "User-Agent": os.environ.get("HH_CHAT_USER_AGENT") or _DEFAULT_UA,
        "Accept": "application/json",
        "Origin": "https://hh.ru",
        "Referer": "https://hh.ru/",
    })
    return s


# ── клиент ────────────────────────────────────────────────────────────────────

class ChatikClient:
    def __init__(self, session: requests.Session, pause: float = _PAUSE):
        self.session = session
        self.pause = pause
        self.rotated: set[str] = set()   # имена cookie, которые hh переустановил

    def _get(self, path: str, params: dict | None = None) -> dict:
        if self.pause:
            time.sleep(self.pause)
        try:
            r = self.session.get(API + path, params=params, timeout=_TIMEOUT,
                                 allow_redirects=False)
        except requests.RequestException as e:
            raise ChatikUnavailable(f"{type(e).__name__}") from None
        code = r.status_code
        # Имена (не значения) cookie, которые hh обновил в ответе: если сессия
        # «вращается», на этапе 2 обновлённые cookie придётся сохранять.
        self.rotated.update(c.name for c in r.cookies)
        if code in (401, 403) or 300 <= code < 400:
            raise SessionExpired(f"HTTP {code}")
        if code != 200:
            raise ChatikUnavailable(f"HTTP {code}")
        if "json" not in (r.headers.get("Content-Type") or "").lower():
            raise SessionExpired("HTML вместо JSON")
        try:
            return r.json()
        except ValueError:
            raise SessionExpired("ответ не JSON") from None

    def list_chats(self) -> tuple[list[dict], dict]:
        """Все чаты постранично (по 20) и chatsDisplayInfo {id: {title, subtitle}}."""
        chats: list[dict] = []
        display: dict = {}
        seen: set = set()
        cursor = None
        for _ in range(_MAX_PAGES):
            params = {"perPage": 20}
            if cursor:
                params["from"] = cursor
            data = self._get("chats", params)
            try:
                items = data["chats"]["items"]
                nxt = data["chats"].get("nextFrom")
            except (KeyError, TypeError):
                raise ChatikUnavailable("неожиданный формат списка чатов") from None
            fresh = [c for c in items if c.get("id") not in seen]
            seen.update(c.get("id") for c in fresh)
            chats.extend(fresh)
            display.update(data.get("chatsDisplayInfo") or {})
            if not nxt or not fresh:
                break
            cursor = nxt
        return chats, display

    def chat_data(self, chat_id) -> dict:
        data = self._get("chat_data", {"chatId": chat_id})
        if not isinstance(data, dict) or "chat" not in data:
            raise ChatikUnavailable("неожиданный формат переписки")
        return data
