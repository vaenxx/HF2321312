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
from middlewares.antispam import AntiSpamMiddleware
from handlers import auth, profile, mod, admin

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_HOST = os.getenv("HF_API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("PORT", os.getenv("HF_API_PORT", "3000")))
_ACTIVATE_ATTEMPTS: dict[str, float] = {}

logging.basicConfig(level=logging.INFO)

async def activate_api(request: web.Request) -> web.Response:
    """Validates only keys already activated in Telegram and returns the bound profile."""
    try:
        payload = await request.json()
        code = db.sanitize_input(payload.get("code"), 128)
        minecraft_username = db.sanitize_input(payload.get("minecraft_username"), 64)
        logging.info("[AUDIT] minecraft_auth username=%s code=%s", minecraft_username or "-", "<hidden>")
        data = await db.load_db()
        key = data.get("keys", {}).get(code)
        if key is None:
            key = next((item for item in data.get("keys", {}).values()
                        if item.get("key") == code or item.get("key_code") == code), None)
        if not key or not key.get("is_used") or key.get("is_active", 1) == 0:
            logging.warning("[AUDIT] minecraft_auth rejected reason=invalid_or_unused")
            return web.json_response({"ok": False, "error": "Ключ ещё не активирован в Telegram или не существует."}, status=403)
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
        request_id = await db.create_login_request(owner_id, code, ip, location)
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
        key = data.get("keys", {}).get(code)
        if key is None:
            key = next((item for item in data.get("keys", {}).values()
                        if item.get("key") == code or item.get("key_code") == code), None)
        owner_id = key.get("used_by") if key else None
        user = data.get("users", {}).get(str(owner_id)) if owner_id is not None else None
        if not key or not key.get("is_used") or key.get("is_active", 1) == 0 or not user or not user.get("is_approved") or user.get("is_banned") or user.get("client_kicked"):
            return web.json_response({"ok": False}, status=403)
        if minecraft_username and user.get("nickname", "").casefold() != minecraft_username.casefold():
            return web.json_response({"ok": False}, status=403)
        user["client_last_seen"] = datetime.now(timezone.utc).isoformat()
        user["client_ip"] = request.headers.get("X-Forwarded-For", request.remote or "unknown").split(",")[0].strip()
        user["client_server"] = db.sanitize_input(payload.get("server"), 128)
        await db.save_db(data)
        return web.json_response({"ok": True})
    except (ValueError, TypeError, KeyError):
        return web.json_response({"ok": False}, status=400)

async def login_status_api(request: web.Request) -> web.Response:
    item = await db.get_login_request(db.sanitize_input(request.match_info.get("request_id"), 64))
    if not item:
        return web.json_response({"ok": False, "error": "Запрос не найден."}, status=404)
    if item.get("status") != "approved":
        return web.json_response({"ok": False, "pending": item.get("status") == "pending", "error": "Вход отклонён." if item.get("status") == "rejected" else "Ожидается подтверждение."}, status=403)
    data = await db.load_db(); user = data.get("users", {}).get(str(item.get("owner_id")), {})
    key = data.get("keys", {}).get(item.get("key_code"), {})
    return web.json_response({"ok": True, "telegram_id": item.get("owner_id"), "nickname": user.get("nickname", key.get("target_nickname", "")),
                              "role": user.get("role", key.get("role", "Стажер")), "mode": user.get("mode", key.get("mode", "")), "expires_at": user.get("expires_at", "")})

async def moderation_event_api(request: web.Request) -> web.Response:
    try:
        payload = await request.json(); code = db.sanitize_input(payload.get("code"), 128)
        data = await db.load_db(); key = data.get("keys", {}).get(code) or next((v for v in data.get("keys", {}).values() if v.get("key") == code or v.get("key_code") == code), None)
        owner_id = key.get("used_by") if key else None; user = data.get("users", {}).get(str(owner_id)) if owner_id else None
        if not key or not user or not user.get("is_approved") or user.get("client_kicked"): return web.json_response({"ok": False}, status=403)
        await db.record_moderation_event(owner_id, payload.get("type", "punishment"), db.sanitize_input(payload.get("target"), 64), db.sanitize_input(payload.get("action"), 64), db.sanitize_input(payload.get("duration"), 32), db.sanitize_input(payload.get("reason"), 300))
        return web.json_response({"ok": True})
    except Exception:
        return web.json_response({"ok": False}, status=400)

async def sessions_api(request: web.Request) -> web.Response:
    data = await db.load_db(); code = db.sanitize_input(request.query.get("code"), 128); key, owner_id, user = await _irc_user(data, code)
    admin_ids = {int(item.strip()) for item in os.getenv("ADMIN_IDS", "").split(",") if item.strip().isdigit()}
    if owner_id not in admin_ids: return web.json_response({"ok": False}, status=403)
    now = datetime.now(timezone.utc); sessions = []
    for item in data.get("users", {}).values():
        try: active = (now - datetime.fromisoformat(item.get("client_last_seen", "")).replace(tzinfo=timezone.utc)).total_seconds() <= 45
        except Exception: active = False
        if active:
            admin_ids = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
            sessions.append({"nickname": item.get("nickname", ""), "role": item.get("role", ""), "title": item.get("irc_title", ""), "is_admin": int(item.get("telegram_id", 0)) in admin_ids, "ip": item.get("client_ip", "-"), "server": item.get("client_server", "-")})
    return web.json_response({"ok": True, "sessions": sessions})

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

async def _irc_user(data: dict, code: str):
    key = data.get("keys", {}).get(code) or next((v for v in data.get("keys", {}).values() if v.get("key") == code or v.get("key_code") == code), None)
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
            await db.clear_irc_title(owner_id); return web.json_response({"ok": True})
        if await db.set_irc_title(owner_id, title): return web.json_response({"ok": True, "title": title})
        return web.json_response({"ok": False, "error": "Титул не найден."}, status=400)
    except Exception: return web.json_response({"ok": False}, status=400)

async def start_api() -> web.AppRunner:
    app = web.Application()
    app.router.add_post("/api/v1/activate", activate_api)
    app.router.add_post("/api/v1/heartbeat", heartbeat_api)
    app.router.add_get("/api/v1/login-status/{request_id}", login_status_api)
    app.router.add_post("/api/v1/event", moderation_event_api)
    app.router.add_get("/api/v1/sessions", sessions_api)
    app.router.add_post("/api/v1/meme/effect", meme_effect_api)
    app.router.add_get("/api/v1/meme/effects", meme_effects_api)
    app.router.add_post("/api/v1/irc/send", irc_send_api)
    app.router.add_post("/api/v1/irc/mute", irc_mute_api)
    app.router.add_get("/api/v1/irc/poll", irc_poll_api)
    app.router.add_get("/api/v1/irc/titles", irc_titles_api)
    app.router.add_post("/api/v1/irc/titles", irc_titles_api)
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
