import asyncio
import logging
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
import aiohttp
from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

import database as db
from runtime_state import RUNTIME_SESSIONS, SCREENSHOT_REQUESTS, SESSION_NOTICES
from middlewares.antispam import AntiSpamMiddleware
from handlers import auth, profile, mod, admin

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_HOST = os.getenv("HF_API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("PORT", os.getenv("HF_API_PORT", "3000")))
_ACTIVATE_ATTEMPTS: dict[str, float] = {}
_RUNTIME_SESSIONS = RUNTIME_SESSIONS
_SCREENSHOT_REQUESTS = SCREENSHOT_REQUESTS
_SESSION_NOTICES = SESSION_NOTICES

logging.basicConfig(level=logging.INFO)


async def prepare_polling(bot: Bot) -> None:
    """Make the bot state compatible with long polling.

    Telegram does not allow ``getUpdates`` while a webhook is configured for
    the same token. The bot is intentionally polling-based, so remove a stale
    webhook before creating the dispatcher. Pending updates are preserved.
    """
    webhook = await bot.get_webhook_info()
    if webhook.url:
        logging.warning(
            "Telegram webhook is active (%s); removing it before polling",
            webhook.url,
        )
        await bot.delete_webhook(drop_pending_updates=False)
        logging.info("Telegram webhook removed; long polling is ready")

async def activate_api(request: web.Request) -> web.Response:
    """Validates only keys already activated in Telegram and returns the bound profile."""
    try:
        payload = await request.json()
        code = db.sanitize_input(payload.get("code"), 128)
        minecraft_username = db.sanitize_input(payload.get("minecraft_username"), 64)
        logging.info("[AUDIT] minecraft_auth username=%s code=%s", minecraft_username or "-", "<hidden>")
        data = await db.load_db()
        stored_code, key = db._find_key(data, code)
        if not key or not db.is_key_used(key) or not db.is_key_active(key):
            logging.warning("[AUDIT] minecraft_auth rejected reason=invalid_or_unused")
            return web.json_response({"ok": False, "error": "Ключ не найден, отключён или ещё не активирован в Telegram."}, status=403)
        owner_id = key.get("used_by")
        user = data.get("users", {}).get(str(owner_id)) if owner_id is not None else None
        if not user or not user.get("is_approved") or user.get("is_banned"):
            logging.warning("[AUDIT] minecraft_auth rejected owner_id=%s reason=profile_denied", owner_id)
            return web.json_response({"ok": False, "error": "Профиль ключа не одобрен или заблокирован."}, status=403)
        expected_name = (user.get("nickname") or key.get("target_nickname", "")).strip().casefold()
        if minecraft_username and expected_name and minecraft_username.casefold() != expected_name:
            return web.json_response({"ok": False, "error": f"Используйте Minecraft-ник {user.get('nickname', '')}."}, status=403)
        ip = request.headers.get("X-Forwarded-For", request.remote or "unknown").split(",")[0].strip()
        now = datetime.now(timezone.utc).timestamp()
        if now - _ACTIVATE_ATTEMPTS.get(ip, 0.0) < 3:
            return web.json_response({"ok": False, "error": "Слишком частые попытки. Подождите несколько секунд."}, status=429)
        _ACTIVATE_ATTEMPTS[ip] = now
        location = await resolve_location(ip)
        request_id = await db.create_login_request(owner_id, stored_code, ip, location)
        logging.info("[AUDIT] login_request created request_id=%s owner_id=%s ip=%s location=%s", request_id, owner_id, ip, location)
        try:
            await bot_instance.send_message(owner_id,
                "🔐 <b>Попытка входа в HF-Moderation</b>\n\n"
                f"🌐 IP: <code>{ip}</code>\n📍 Место: <b>{location}</b>\n"
                f"🎮 Minecraft: <code>{minecraft_username or 'не указан'}</code>\n\n"
                "Разрешить вход?",
                parse_mode="HTML", reply_markup=login_request_kb(request_id))
        except Exception:
            return web.json_response({"ok": False, "error": "Не удалось доставить запрос в Telegram."}, status=503)
        return web.json_response({"ok": False, "pending": True, "request_id": request_id,
                                  "error": "Ожидается подтверждение входа в Telegram."}, status=202)
    except (ValueError, TypeError, KeyError):
        return web.json_response({"ok": False, "error": "Некорректный запрос."}, status=400)

def login_request_kb(request_id: str):
    return {"inline_keyboard": [[{"text": "✅ Принять", "callback_data": f"login_approve_{request_id}"},
                                  {"text": "❌ Отклонить", "callback_data": f"login_reject_{request_id}"}]]}

bot_instance = None

async def resolve_location(ip: str) -> str:
    if ip in {"127.0.0.1", "::1", "unknown"} or ip.startswith(("10.", "192.168.", "172.16.")):
        return "🏠 локальная сеть"
    try:
        timeout = aiohttp.ClientTimeout(total=2)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"https://ipapi.co/{ip}/json/") as response:
                data = await response.json()
        country = data.get("country_name") or "страна не определена"
        code = (data.get("country_code") or "").upper()
        emoji = "".join(chr(127397 + ord(char)) for char in code) if len(code) == 2 else "🌐"
        city = data.get("city") or "город не определён"
        return f"{emoji} {country}, {city}"
    except Exception:
        return "🌐 страна не определена"

async def heartbeat_api(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        code = db.sanitize_input(payload.get("code"), 128)
        minecraft_username = db.sanitize_input(payload.get("minecraft_username"), 64)
        data = await db.load_db()
        _, key = db._find_key(data, code)
        owner_id = key.get("used_by") if key else None
        user = data.get("users", {}).get(str(owner_id)) if owner_id is not None else None
        if not key or not db.is_key_used(key) or not db.is_key_active(key) or not user or not user.get("is_approved") or user.get("is_banned") or user.get("client_kicked"):
            return web.json_response({"ok": False}, status=403)
        if minecraft_username and user.get("nickname", "").casefold() != minecraft_username.casefold():
            return web.json_response({"ok": False}, status=403)
        client_ip = request.headers.get("X-Forwarded-For", request.remote or "unknown").split(",")[0].strip()
        client_server = db.sanitize_input(payload.get("server"), 128)
        client_mode = db.sanitize_input(payload.get("mode"), 128)
        owner = int(owner_id)
        previous = _RUNTIME_SESSIONS.get(owner, {})
        _RUNTIME_SESSIONS[owner] = {"telegram_id": owner, "nickname": user.get("nickname", ""), "role": user.get("role", ""), "ip": client_ip, "server": client_server, "mode": client_mode or user.get("mode", "-"), "last_seen": datetime.now(timezone.utc).timestamp(), "title": user.get("irc_title", ""), "tab_prefix": user.get("tab_prefix", ""), "tab_suffix": user.get("tab_suffix", ""), "cosmetics": previous.get("cosmetics", [])}
        logging.info("[SESSION] heartbeat owner=%s nickname=%s server=%s", owner, user.get("nickname", "-"), client_server or "-")
        # Keep persistent profile data intact, but session presence itself is
        # runtime-only and therefore never resurrects after a bot restart.
        requester = _SCREENSHOT_REQUESTS.get(owner)
        if requester:
            logging.info("[SCREENSHOT] delivered request owner=%s requester=%s", owner, requester)
        notice = _SESSION_NOTICES.pop(owner, None)
        return web.json_response({"ok": True, "screenshot_request": bool(requester), "session_notice": notice})
    except (ValueError, TypeError, KeyError):
        return web.json_response({"ok": False}, status=400)

async def login_status_api(request: web.Request) -> web.Response:
    item = await db.get_login_request(db.sanitize_input(request.match_info.get("request_id"), 64))
    if not item:
        return web.json_response({"ok": False, "error": "Запрос не найден."}, status=404)
    if item.get("status") != "approved":
        return web.json_response({"ok": False, "pending": item.get("status") == "pending", "error": "Вход отклонён." if item.get("status") == "rejected" else "Ожидается подтверждение."}, status=403)
    data = await db.load_db(); user = data.get("users", {}).get(str(item.get("owner_id")), {})
    _, key = db._find_key(data, item.get("key_code", ""))
    key = key or {}
    return web.json_response({"ok": True, "telegram_id": item.get("owner_id"), "nickname": user.get("nickname", key.get("target_nickname", "")),
                              "role": user.get("role", key.get("role", "Стажер")), "mode": user.get("mode", key.get("mode", "")), "expires_at": user.get("expires_at", "")})

async def moderation_event_api(request: web.Request) -> web.Response:
    try:
        payload = await request.json(); code = db.sanitize_input(payload.get("code"), 128)
        data = await db.load_db(); _, key = db._find_key(data, code)
        owner_id = key.get("used_by") if key else None; user = data.get("users", {}).get(str(owner_id)) if owner_id else None
        if not key or not db.is_key_active(key) or not db.is_key_used(key) or not user or not user.get("is_approved") or user.get("client_kicked"): return web.json_response({"ok": False}, status=403)
        await db.record_moderation_event(owner_id, payload.get("type", "punishment"), db.sanitize_input(payload.get("target"), 64), db.sanitize_input(payload.get("action"), 64), db.sanitize_input(payload.get("duration"), 32), db.sanitize_input(payload.get("reason"), 300))
        return web.json_response({"ok": True})
    except Exception:
        return web.json_response({"ok": False}, status=400)

async def sessions_api(request: web.Request) -> web.Response:
    data = await db.load_db(); code = db.sanitize_input(request.query.get("code"), 128); key, owner_id, user = await _irc_user(data, code)
    if not key or not user or not user.get("is_approved") or user.get("client_kicked"):
        return web.json_response({"ok": False}, status=403)
    now = datetime.now(timezone.utc).timestamp(); sessions = []
    admin_ids = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
    for item in list(_RUNTIME_SESSIONS.values()):
        if now - float(item.get("last_seen", 0)) <= 45:
            sessions.append({"nickname": item.get("nickname", ""), "role": item.get("role", ""), "title": item.get("title", ""), "tab_prefix": item.get("tab_prefix", ""), "tab_suffix": item.get("tab_suffix", ""), "is_admin": int(item.get("telegram_id", 0)) in admin_ids, "ip": item.get("ip", "-"), "server": item.get("server", "-"), "mode": item.get("mode", "-")})
    return web.json_response({"ok": True, "sessions": sessions})

async def online_staff_api(request: web.Request) -> web.Response:
    data = await db.load_db(); code = db.sanitize_input(request.query.get("code"), 128); key, owner_id, user = await _irc_user(data, code)
    if not key or not user or not user.get("is_approved") or user.get("client_kicked"): return web.json_response({"ok": False}, status=403)
    # This endpoint is used for the HFM nametag/TAB marker.  A moderator can
    # be offline or have no active heartbeat and must still be recognizable
    # by another client as soon as the matching nickname is on the server.
    names = []
    for item in data.get("users", {}).values():
        if item.get("is_approved") and not item.get("is_banned") and item.get("nickname"):
            names.append(item.get("nickname"))
    return web.json_response({"ok": True, "nicknames": names})

async def screenshot_request_api(request: web.Request) -> web.Response:
    try:
        payload = await request.json(); data = await db.load_db()
        key, owner_id, user = await _irc_user(data, db.sanitize_input(payload.get("code"), 128))
        requester = int(payload.get("requester_id", 0))
        admins = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
        if not key or not user or owner_id is None or requester not in admins:
            return web.json_response({"ok": False}, status=403)
        _SCREENSHOT_REQUESTS[int(owner_id)] = requester
        return web.json_response({"ok": True})
    except Exception:
        return web.json_response({"ok": False}, status=400)

async def screenshot_upload_api(request: web.Request) -> web.Response:
    reader = await request.multipart(); code = ""; content = None
    async for part in reader:
        if part.name == "code": code = db.sanitize_input(await part.text(), 128)
        elif part.name == "screenshot": content = await part.read(decode=False)
    data = await db.load_db(); key, owner_id, user = await _irc_user(data, code)
    requester = _SCREENSHOT_REQUESTS.pop(int(owner_id), None) if owner_id is not None else None
    if not key or not user or requester is None or not content or len(content) > 12 * 1024 * 1024:
        logging.warning("[SCREENSHOT] upload rejected owner=%s requester=%s bytes=%s", owner_id, requester, len(content or b""))
        return web.json_response({"ok": False}, status=403)
    try:
        from aiogram.types import BufferedInputFile
        await bot_instance.send_photo(requester, BufferedInputFile(content, filename="hf-screenshot.png"), caption=f"📸 Скриншот модератора {user.get('nickname', '')}")
        logging.info("[SCREENSHOT] sent owner=%s requester=%s bytes=%s", owner_id, requester, len(content))
        return web.json_response({"ok": True})
    except Exception:
        return web.json_response({"ok": False}, status=503)

async def meme_effect_api(request: web.Request) -> web.Response:
    try:
        payload = await request.json(); data = await db.load_db(); key, owner_id, user = await _irc_user(data, db.sanitize_input(payload.get("code"), 128))
        if not key or not user or not user.get("is_approved") or user.get("client_kicked"): return web.json_response({"ok": False}, status=403)
        if payload.get("active", True): await db.set_meme_effect(owner_id, db.sanitize_input(payload.get("scenario"), 32), db.sanitize_input(payload.get("target"), 64), int(payload.get("started_at", 0)))
        else: await db.remove_meme_effect(owner_id)
        return web.json_response({"ok": True})
    except Exception: return web.json_response({"ok": False}, status=400)

async def meme_effects_api(request: web.Request) -> web.Response:
    try:
        data = await db.load_db(); key, owner_id, user = await _irc_user(data, db.sanitize_input(request.query.get("code"), 128))
        if not key or not user or not user.get("is_approved"): return web.json_response({"ok": False}, status=403)
        effects = [item for item in data.get("meme_effects", {}).values() if item.get("owner_id") != owner_id and datetime.now().timestamp() - float(item.get("updated_at", 0)) < 10]
        return web.json_response({"ok": True, "effects": effects})
    except Exception: return web.json_response({"ok": False}, status=400)

async def cosmetics_state_api(request: web.Request) -> web.Response:
    """Stores the equipped cosmetic indices for the current runtime session."""
    try:
        payload = await request.json()
        data = await db.load_db()
        key, owner_id, user = await _irc_user(data, db.sanitize_input(payload.get("code"), 128))
        if not key or owner_id is None or not user or not user.get("is_approved") or user.get("client_kicked"):
            return web.json_response({"ok": False}, status=403)
        # The authenticated profile is authoritative; never let a client
        # advertise cosmetics under another moderator's nickname.
        nickname = user.get("nickname", "")
        server = db.sanitize_input(payload.get("server"), 128)
        raw = payload.get("cosmetics", [])
        cosmetics = []
        if isinstance(raw, list):
            for value in raw:
                try:
                    index = int(value)
                except (TypeError, ValueError):
                    continue
                if 0 <= index <= 255 and index not in cosmetics:
                    cosmetics.append(index)
        session = _RUNTIME_SESSIONS.get(int(owner_id))
        if session is None:
            session = {"telegram_id": int(owner_id), "nickname": nickname or user.get("nickname", ""), "server": server}
            _RUNTIME_SESSIONS[int(owner_id)] = session
        session["cosmetics"] = cosmetics
        if nickname:
            session["nickname"] = nickname
        if server:
            session["server"] = server
        session["last_seen"] = datetime.now(timezone.utc).timestamp()
        return web.json_response({"ok": True})
    except Exception:
        return web.json_response({"ok": False}, status=400)

async def cosmetics_states_api(request: web.Request) -> web.Response:
    """Returns equipped cosmetics of approved moderators on the same server."""
    try:
        data = await db.load_db()
        key, owner_id, user = await _irc_user(data, db.sanitize_input(request.query.get("code"), 128))
        if not key or owner_id is None or not user or not user.get("is_approved") or user.get("client_kicked"):
            return web.json_response({"ok": False}, status=403)
        server = db.sanitize_input(request.query.get("server"), 128)
        now = datetime.now(timezone.utc).timestamp()
        states = []
        for session in _RUNTIME_SESSIONS.values():
            if now - float(session.get("last_seen", 0)) > 45:
                continue
            if server and session.get("server", "") != server:
                continue
            cosmetics = session.get("cosmetics", [])
            if not isinstance(cosmetics, list):
                cosmetics = []
            states.append({"nickname": session.get("nickname", ""), "cosmetics": cosmetics})
        return web.json_response({"ok": True, "states": states})
    except Exception:
        return web.json_response({"ok": False}, status=400)

async def _irc_user(data: dict, code: str):
    _, key = db._find_key(data, code)
    if not key or not db.is_key_active(key) or not db.is_key_used(key):
        return None, None, None
    owner_id = key.get("used_by") if key else None; user = data.get("users", {}).get(str(owner_id)) if owner_id else None
    return key, owner_id, user

async def irc_send_api(request: web.Request) -> web.Response:
    try:
        payload = await request.json(); data = await db.load_db(); key, owner_id, user = await _irc_user(data, db.sanitize_input(payload.get("code"), 128))
        if not key or not user or not user.get("is_approved") or user.get("client_kicked"): return web.json_response({"ok": False}, status=403)
        admin_ids = [int(item.strip()) for item in os.getenv("ADMIN_IDS", "").split(",") if item.strip().isdigit()]
        target = db.sanitize_input(payload.get("target"), 64).lstrip("@")
        recipient_id = None
        if target:
            target_user = next((item for item in data.get("users", {}).values() if item.get("username", "").casefold() == target.casefold() or item.get("nickname", "").casefold() == target.casefold()), None)
            if not target_user: return web.json_response({"ok": False, "error": "Пользователь не найден."}, status=404)
            recipient_id = target_user.get("telegram_id")
        title = db.sanitize_input(payload.get("title"), 500)
        if title and title in db.IRC_TITLES:
            user["irc_title"] = title
            await db.save_db(data)
        else:
            title = user.get("irc_title", "")
        message_id = await db.add_irc_message(owner_id, user.get("nickname", ""), user.get("role", ""), payload.get("text", ""), owner_id in admin_ids, recipient_id, title)
        return web.json_response({"ok": True, "id": message_id})
    except Exception: return web.json_response({"ok": False}, status=400)

async def irc_mute_api(request: web.Request) -> web.Response:
    try:
        payload = await request.json(); data = await db.load_db(); key, owner_id, user = await _irc_user(data, db.sanitize_input(payload.get("code"), 128))
        if not key or not user or not user.get("is_approved"): return web.json_response({"ok": False}, status=403)
        admin_ids = {int(item.strip()) for item in os.getenv("ADMIN_IDS", "").split(",") if item.strip().isdigit()}
        if int(owner_id) not in admin_ids:
            return web.json_response({"ok": False, "error": "Только администратор может выдавать IRC-мут."}, status=403)
        target = db.sanitize_input(payload.get("target"), 64).lstrip("@")
        duration = db.sanitize_input(payload.get("duration"), 16)
        reason = db.sanitize_input(payload.get("reason"), 300) or "Без причины"
        target_user = next((item for item in data.get("users", {}).values()
                            if item.get("username", "").casefold() == target.casefold()
                            or item.get("nickname", "").casefold() == target.casefold()), None)
        if not target_user:
            return web.json_response({"ok": False, "error": "Пользователь не найден."}, status=404)
        await db.set_irc_mute(owner_id, target, duration, reason)
        target_id = int(target_user.get("telegram_id"))
        issuer = db.sanitize_input(user.get("nickname") or user.get("username") or "администратор", 64)
        await db.add_irc_message(0, "Система", "IRC", f"Вас замутил {issuer} в IRC на {duration}. Причина: {reason}", False, target_id)
        return web.json_response({"ok": True})
    except Exception: return web.json_response({"ok": False}, status=400)

async def irc_poll_api(request: web.Request) -> web.Response:
    try:
        data = await db.load_db(); key, owner_id, user = await _irc_user(data, db.sanitize_input(request.query.get("code"), 128))
        if not key or not user or not user.get("is_approved") or user.get("client_kicked"): return web.json_response({"ok": False}, status=403)
        return web.json_response({"ok": True, "messages": await db.get_irc_messages(int(request.query.get("after", "0")), owner_id)})
    except Exception: return web.json_response({"ok": False}, status=400)

async def irc_titles_api(request: web.Request) -> web.Response:
    try:
        data = await db.load_db(); key, owner_id, user = await _irc_user(data, db.sanitize_input(request.query.get("code"), 128))
        if not key or not user or not user.get("is_approved"):
            return web.json_response({"ok": False}, status=403)
        if request.method == "GET":
            return web.json_response({"ok": True, "titles": db.IRC_TITLES, "selected": user.get("irc_title", "")})
        payload = await request.json(); title = db.sanitize_input(payload.get("title"), 500)
        if title.casefold() == "off":
            await db.clear_irc_title(owner_id)
            session = _RUNTIME_SESSIONS.get(int(owner_id))
            if session is not None: session["title"] = ""
            return web.json_response({"ok": True})
        if await db.set_irc_title(owner_id, title):
            session = _RUNTIME_SESSIONS.get(int(owner_id))
            if session is not None: session["title"] = title
            return web.json_response({"ok": True, "title": title})
        return web.json_response({"ok": False, "error": "Титул не найден."}, status=400)
    except Exception: return web.json_response({"ok": False}, status=400)

async def irc_prefix_api(request: web.Request) -> web.Response:
    try:
        payload = await request.json() if request.method != "GET" else {}
        raw_code = request.query.get("code") if request.method == "GET" else payload.get("code")
        data = await db.load_db(); key, owner_id, user = await _irc_user(data, db.sanitize_input(raw_code, 128))
        if not key or owner_id is None or not user:
            logging.warning("[TAB_PREFIX] rejected: key/profile is not linked")
            return web.json_response({"ok": False, "error": "Ключ не привязан к профилю. Заново активируй мод командой .code."}, status=403)
        if not user.get("is_approved") or user.get("is_banned") or user.get("client_kicked"):
            logging.warning("[TAB_PREFIX] rejected: profile inactive or access revoked owner=%s", owner_id)
            return web.json_response({"ok": False, "error": "Профиль неактивен или доступ отозван."}, status=403)
        if request.method == "GET":
            return web.json_response({"ok": True, "prefix": user.get("tab_prefix", "")})
        prefix = db.sanitize_input(payload.get("prefix"), 500)
        if prefix.casefold() == "off":
            user.pop("tab_prefix", None)
            prefix = ""
        elif not db.is_valid_custom_irc_title(prefix):
            return web.json_response({"ok": False, "error": "Некорректный префикс или превышен лимит 30 символов."}, status=400)
        else:
            user["tab_prefix"] = prefix
        await db.save_db(data)
        session = _RUNTIME_SESSIONS.get(int(owner_id))
        if session is not None:
            session["tab_prefix"] = prefix
        return web.json_response({"ok": True, "prefix": prefix})
    except Exception:
        logging.exception("[TAB_PREFIX] request failed")
        return web.json_response({"ok": False, "error": "Внутренняя ошибка API префикса."}, status=400)

async def irc_reset_api(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        data = await db.load_db()
        key, owner_id, user = await _irc_user(data, db.sanitize_input(payload.get("code"), 128))
        if not key or owner_id is None or not user or not user.get("is_approved") \
                or user.get("is_banned") or user.get("client_kicked"):
            return web.json_response({"ok": False, "error": "Профиль неактивен или доступ отозван."}, status=403)

        for field in ("irc_title", "tab_prefix", "tab_suffix"):
            user.pop(field, None)
        await db.save_db(data)

        session = _RUNTIME_SESSIONS.get(int(owner_id))
        if session is not None:
            session["title"] = ""
            session["tab_prefix"] = ""
            session["tab_suffix"] = ""
        return web.json_response({"ok": True})
    except Exception:
        logging.exception("[IRC_RESET] request failed")
        return web.json_response({"ok": False, "error": "Внутренняя ошибка сброса оформления."}, status=400)

async def admin_tab_decoration_api(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        data = await db.load_db()
        key, owner_id, admin_user = await _irc_user(data, db.sanitize_input(payload.get("code"), 128))
        admin_ids = {int(item.strip()) for item in os.getenv("ADMIN_IDS", "").split(",") if item.strip().isdigit()}
        if not key or owner_id is None or not admin_user or not admin_user.get("is_approved") or admin_user.get("is_banned") or admin_user.get("client_kicked"):
            logging.warning("[TAB_DECORATION] rejected: admin profile inactive")
            return web.json_response({"ok": False, "error": "Нет активной авторизованной сессии."}, status=403)
        caller_is_admin = int(owner_id) in admin_ids
        now = datetime.now(timezone.utc).timestamp()
        caller_session = _RUNTIME_SESSIONS.get(int(owner_id))
        if caller_session is None or now - float(caller_session.get("last_seen", 0)) > 45:
            logging.warning("[TAB_DECORATION] rejected: caller session is not online owner=%s", owner_id)
            return web.json_response({"ok": False, "error": "Нет активной сессии модератора."}, status=403)

        target_name = db.sanitize_input(payload.get("target"), 64)
        if not target_name:
            return web.json_response({"ok": False, "error": "Укажи ник активного модератора."}, status=400)
        field = db.sanitize_input(payload.get("field"), 16).casefold()
        value = db.sanitize_input(payload.get("value"), 500)
        if field not in {"prefix", "suffix"}:
            return web.json_response({"ok": False, "error": "Неизвестный тип оформления."}, status=400)
        if value.casefold() == "off":
            value = ""
        elif not db.is_valid_custom_irc_title(value):
            return web.json_response({"ok": False, "error": "Некорректный текст или превышен лимит 30 символов."}, status=400)

        active = next((session for session in _RUNTIME_SESSIONS.values()
                       if str(session.get("nickname") or "").casefold() == target_name.casefold()
                       and now - float(session.get("last_seen", 0)) <= 45), None)
        if active is None:
            logging.warning("[TAB_DECORATION] rejected: target session not online target=%s", target_name)
            return web.json_response({"ok": False, "error": "Модератор не найден среди активных сессий."}, status=404)
        target_id = int(active.get("telegram_id", 0))
        self_clear = target_id == int(owner_id) and not value
        if not caller_is_admin and not self_clear:
            logging.warning("[TAB_DECORATION] rejected: caller is not an admin or clearing own field owner=%s", owner_id)
            return web.json_response({"ok": False, "error": "Команда доступна только администраторам бота."}, status=403)
        target_user = data.get("users", {}).get(str(target_id))
        if not target_user or not target_user.get("is_approved") or target_user.get("is_banned") or target_user.get("client_kicked"):
            return web.json_response({"ok": False, "error": "Профиль модератора неактивен."}, status=404)

        db_field = "tab_prefix" if field == "prefix" else "tab_suffix"
        if value:
            target_user[db_field] = value
        else:
            target_user.pop(db_field, None)
        await db.save_db(data)
        active[db_field] = value
        return web.json_response({"ok": True, "target": target_name, "field": field, "value": value})
    except Exception:
        logging.exception("[TAB_DECORATION] request failed")
        return web.json_response({"ok": False, "error": "Внутренняя ошибка API оформления."}, status=400)

async def start_api() -> web.AppRunner:
    app = web.Application()
    app.router.add_post("/api/v1/activate", activate_api)
    app.router.add_post("/api/v1/heartbeat", heartbeat_api)
    app.router.add_get("/api/v1/login-status/{request_id}", login_status_api)
    app.router.add_post("/api/v1/event", moderation_event_api)
    app.router.add_get("/api/v1/sessions", sessions_api)
    app.router.add_get("/api/v1/staff/online", online_staff_api)
    app.router.add_post("/api/v1/screenshot/request", screenshot_request_api)
    app.router.add_post("/api/v1/screenshot/upload", screenshot_upload_api)
    app.router.add_post("/api/v1/meme/effect", meme_effect_api)
    app.router.add_get("/api/v1/meme/effects", meme_effects_api)
    app.router.add_post("/api/v1/cosmetics/state", cosmetics_state_api)
    app.router.add_get("/api/v1/cosmetics/states", cosmetics_states_api)
    app.router.add_post("/api/v1/irc/send", irc_send_api)
    app.router.add_post("/api/v1/irc/mute", irc_mute_api)
    app.router.add_get("/api/v1/irc/poll", irc_poll_api)
    app.router.add_get("/api/v1/irc/titles", irc_titles_api)
    app.router.add_post("/api/v1/irc/titles", irc_titles_api)
    app.router.add_get("/api/v1/irc/prefix", irc_prefix_api)
    app.router.add_post("/api/v1/irc/prefix", irc_prefix_api)
    app.router.add_post("/api/v1/irc/reset", irc_reset_api)
    app.router.add_post("/api/v1/admin/tab-decoration", admin_tab_decoration_api)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, API_HOST, API_PORT).start()
    except OSError as exc:
        await runner.cleanup()
        logging.error("Не удалось открыть API-порт %s: %s", API_PORT, exc)
        return None
    logging.info("HF API listening on %s:%s", API_HOST, API_PORT)
    return runner

async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не установлен в .env файле!")

    # Инициализация JSON БД
    await db.init_db()
    logging.info("[AUDIT] database path=%s", db.DB_PATH)

    # Фоновая автопроверка БД раз в 10 секунд
    asyncio.create_task(db.auto_reload_db_task(interval=10))

    bot = Bot(token=BOT_TOKEN)
    global bot_instance
    bot_instance = bot
    await prepare_polling(bot)
    api_runner = await start_api()
    dp = Dispatcher(storage=MemoryStorage())

    # 🛡️ ПОДКЛЮЧЕНИЕ АНТИСПАМ ЗАЩИТЫ (0.7 сек задержка)
    dp.message.outer_middleware(AntiSpamMiddleware(limit=0.7))
    dp.callback_query.outer_middleware(AntiSpamMiddleware(limit=0.7))

    # Подключение роутеров
    dp.include_router(auth.router)
    dp.include_router(profile.router)
    dp.include_router(mod.router)
    # Keep startup compatible with older deployments that called this router
    # ``admin_router`` while the current handler module exposes ``router``.
    admin_router = getattr(admin, "router", None) or getattr(admin, "admin_router", None)
    if admin_router is None:
        logging.error("Модуль handlers.admin загружен без Router; админ-панель отключена")
    else:
        dp.include_router(admin_router)

    logging.info("🚀 Бот запущен со встроенной системой защиты от спама!")
    try:
        await dp.start_polling(bot)
    finally:
        if api_runner is not None:
            await api_runner.cleanup()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
