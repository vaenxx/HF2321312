import asyncio, json, os, tempfile, uuid, logging, shutil
from datetime import datetime
from pathlib import Path
from typing import Any
import aiofiles
from dotenv import load_dotenv

# Load hosting/local variables before resolving the persistent database path.
load_dotenv(Path(__file__).resolve().parent / ".env")

_DATA_DIR = os.getenv("DATA_DIR", "").strip()
DB_PATH = (Path(_DATA_DIR) / "database.json") if _DATA_DIR else (Path(__file__).resolve().parent / "database.json")
SOURCE_DB_PATH = Path(__file__).resolve().parent / "database.json"
_LOCK = asyncio.Lock()
ADMIN_ROLES = []
ALL_ROLES = ["HW: Стажер", "HW: Мл. Сотрудник", "HW: Сотрудник", "HW: Мл.Спектатор", "HW: Спектатор", "HW: Ст.Сотрудник",
             "FT: Стажер", "FT: Staff", "FT: Агент", "Зам Куратора", "Куратор", "Админ", "СтАдмин", "Владелец"]
ALL_MODES = ["FunTime", "HolyWorld", "ReallyWorld"]

def _default() -> dict[str, Any]: return {"users": {}, "keys": {}, "applications": [], "mod_versions": [], "stats": {}}
def sanitize_input(text: str | None, max_length: int = 100) -> str: return str(text or "").strip()[:max_length]
def _normalize(data: dict | None) -> dict:
    out = _default(); out.update(data or {})
    for k, v in _default().items():
        if not isinstance(out.get(k), type(v)): out[k] = v.copy() if isinstance(v, dict) else []
    for code, key in out["keys"].items(): key.setdefault("key_code", code); key.setdefault("days", 30); key.setdefault("is_used", 0)
    for user in out["users"].values():
        user.setdefault("modes", [user.get("mode", "HolyWorld")])
    # Migrate keys activated by older bot versions: bind them to the unique
    # profile with the same target nickname when no owner was stored yet.
    for code, key in out["keys"].items():
        if key.get("is_used") and not key.get("used_by"):
            matches = [(uid, user) for uid, user in out["users"].items()
                       if user.get("nickname") == key.get("target_nickname")]
            if len(matches) == 1:
                uid, user = matches[0]
                key["used_by"] = int(uid)
                user.setdefault("key_code", code)
                user.setdefault("days", key.get("days", 30))
    for mod in out["mod_versions"]:
        mod.setdefault("allowed_roles", mod.get("roles", ALL_ROLES.copy())); mod.setdefault("created_at", "")
    return out

async def load_db() -> dict:
    if not DB_PATH.exists():
        data = _default(); await save_db(data); return data
    try:
        async with aiofiles.open(DB_PATH, "r", encoding="utf-8") as f: content = await f.read()
        return _normalize(json.loads(content) if content.strip() else None)
    except (OSError, json.JSONDecodeError): return _default()

async def _write(data: dict) -> None:
    if DB_PATH.exists():
        backup = DB_PATH.with_suffix(".json.bak")
        try: shutil.copy2(DB_PATH, backup)
        except OSError: pass
    fd, name = tempfile.mkstemp(prefix="database.", suffix=".tmp", dir=DB_PATH.parent); os.close(fd)
    try:
        async with aiofiles.open(name, "w", encoding="utf-8") as f: await f.write(json.dumps(_normalize(data), ensure_ascii=False, indent=2))
        os.replace(name, DB_PATH)
    finally:
        if os.path.exists(name): os.unlink(name)

async def save_db(data: dict) -> None:
    async with _LOCK: await _write(data)
async def init_db() -> None:
    """Initialize persistent storage without destroying an existing database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        backup = DB_PATH.with_suffix(".json.bak")
        if backup.exists():
            shutil.copy2(backup, DB_PATH)
        elif SOURCE_DB_PATH.exists() and SOURCE_DB_PATH.resolve() != DB_PATH.resolve():
            shutil.copy2(SOURCE_DB_PATH, DB_PATH)
        else:
            await save_db(_default())
            return
    # A deployment can copy a fresh template database over the persistent file.
    # If that template is empty, prefer the previous atomic-write backup.
    backup = DB_PATH.with_suffix(".json.bak")
    try:
        async with aiofiles.open(DB_PATH, "r", encoding="utf-8") as file:
            content = await file.read()
        data = _normalize(json.loads(content) if content.strip() else None)
        if not data.get("users") and not data.get("keys") and backup.exists():
            async with aiofiles.open(backup, "r", encoding="utf-8") as file:
                previous = _normalize(json.loads(await file.read()))
            if previous.get("users") or previous.get("keys"):
                shutil.copy2(backup, DB_PATH)
                data = previous
    except (OSError, json.JSONDecodeError):
        if backup.exists():
            shutil.copy2(backup, DB_PATH)
            return
        raise RuntimeError(f"База данных повреждена: {DB_PATH}")
    # Normalize old records in place, preserving all user/key/event data.
    await save_db(data)
async def auto_reload_db_task(interval: int = 10) -> None:
    while True:
        await asyncio.sleep(interval)

def _find_key(db: dict, code: str):
    clean = sanitize_input(code, 128)
    direct = db["keys"].get(clean)
    if direct is not None:
        return clean, direct
    for stored_code, value in db["keys"].items():
        if value.get("key") == clean or value.get("key_code") == clean:
            return stored_code, value
    return None, None

async def get_key(code: str) -> dict | None:
    return _find_key(await load_db(), code)[1]
async def redeem_key(code: str, telegram_id: int) -> dict | None:
    async with _LOCK:
        db = await load_db(); stored_code, key = _find_key(db, code)
        logging.info("[AUDIT] redeem_key found=%s stored_id=%s is_used=%s used_by=%s requester=%s",
                     bool(key), stored_code or "-", key.get("is_used") if key else "-",
                     key.get("used_by") if key else "-", telegram_id)
        if not key: return None
        if key.get("is_used"):
            # Повторный вход разрешен только тому Telegram-пользователю,
            # который уже активировал этот ключ ранее.
            owner_match = key.get("used_by") == telegram_id
            if not owner_match:
                profile = db.get("users", {}).get(str(telegram_id), {})
                entered = sanitize_input(code, 128)
                owner_match = profile.get("key_code") in {entered, stored_code, key.get("key"), key.get("key_code")}
                if owner_match:
                    key["used_by"] = telegram_id
                    await _write(db)
            return dict(key) if owner_match else None
        key["is_used"], key["used_by"] = 1, telegram_id; key["used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); await _write(db); return dict(key)
async def mark_key_used(code: str) -> None:
    db = await load_db();
    if code in db["keys"]: db["keys"][code]["is_used"] = 1; await save_db(db)

async def reset_key_binding(code: str) -> bool:
    """Сбрасывает владельца ключа и делает его снова доступным для активации."""
    db = await load_db()
    key = db["keys"].get(code)
    if not key:
        return False
    key["is_used"] = 0
    key.pop("used_by", None)
    key.pop("used_at", None)
    await save_db(db)
    return True
async def get_all_keys(limit=15, offset=0):
    db = await load_db(); out=[]
    for code, value in db["keys"].items(): item=dict(value); item["key_code"]=code; out.append(item)
    return out[offset:offset+limit]
async def find_key_by_prefix(prefix): return next((k for k in (await load_db())["keys"] if k.startswith(prefix)), None)
async def delete_key(code): db=await load_db(); db["keys"].pop(code, None); await save_db(db)
async def create_key(target_nickname, role, mode, days):
    db=await load_db(); code=f"HF-{uuid.uuid4().hex[:12].upper()}"; db["keys"][code]={"key":code,"key_code":code,"target_nickname":sanitize_input(target_nickname,32),"role":role,"mode":mode,"days":max(1,int(days)),"is_used":0,"created_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S")}; await save_db(db); return code

async def get_user(tg): return (await load_db())["users"].get(str(tg))
async def get_all_users(limit=15, offset=0): return list((await load_db())["users"].values())[offset:offset+limit]
async def create_user(telegram_id, username, nickname, role, mode, is_approved=0, days=0, key_code=""):
    db=await load_db(); old=db["users"].get(str(telegram_id), {}); db["users"][str(telegram_id)]={**old,"telegram_id":telegram_id,"username":sanitize_input(username,64),"nickname":sanitize_input(nickname,32),"role":role,"mode":mode,"modes":old.get("modes", [mode]),"discord_id":old.get("discord_id",""),"discord_tag":old.get("discord_tag",""),"is_approved":int(is_approved),"is_banned":old.get("is_banned",0),"days":days,"key_code":key_code,"created_at":old.get("created_at",datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}; db["stats"].setdefault(str(telegram_id),{"user_id":telegram_id,"bans":0,"mutes":0,"checks":0}); await save_db(db)
async def approve_user(tg):
    db=await load_db(); user=db["users"].get(str(tg));
    if user: user["is_approved"]=1; [a.update(status="approved") for a in db["applications"] if a.get("user_id")==tg and a.get("status")=="pending"]; await save_db(db)
async def set_user_key(tg, code, days=None):
    db=await load_db(); user=db["users"].get(str(tg));
    if user: user["key_code"]=code; user["days"]=days if days is not None else user.get("days",0); user["activated_at"]=datetime.now().strftime("%Y-%m-%d %H:%M:%S"); await save_db(db)

async def set_user_role(tg: int, role: str) -> bool:
    db = await load_db(); user = db["users"].get(str(tg))
    if not user or role not in ALL_ROLES:
        return False
    user["role"] = role
    await save_db(db)
    return True

async def extend_user_key(tg: int, days: int) -> bool:
    db = await load_db(); user = db["users"].get(str(tg))
    if not user or not user.get("key_code"): return False
    code, key = _find_key(db, user["key_code"])
    if not key: return False
    key["days"] = int(days); key["is_used"] = 1; key["is_active"] = 1; user["days"] = int(days)
    await save_db(db); return True

async def deactivate_user_key(tg: int) -> bool:
    db = await load_db(); user = db["users"].get(str(tg));
    if not user: return False
    code, key = _find_key(db, user.get("key_code", ""))
    if key: key["is_active"] = 0
    user["is_approved"] = 0
    await save_db(db); return True

async def delete_user_account(tg: int, delete_key: bool) -> bool:
    db = await load_db(); user = db["users"].pop(str(tg), None)
    if not user: return False
    if delete_key:
        code, key = _find_key(db, user.get("key_code", ""))
        if code: db["keys"].pop(code, None)
    await save_db(db); return True

async def get_moderation_logs(tg: int, kind: str = "all") -> list[dict]:
    db = await load_db(); result = []
    if kind in ("all", "punishments"): result.extend(db.get("punishment_logs", {}).get(str(tg), []))
    if kind in ("all", "checks"): result.extend(db.get("check_logs", {}).get(str(tg), []))
    return list(reversed(result))

async def toggle_user_mode(tg: int, mode: str) -> tuple[bool, list[str]]:
    db = await load_db(); user = db["users"].get(str(tg))
    if not user or mode not in ALL_MODES: return False, []
    modes = [item for item in user.get("modes", [user.get("mode", mode)]) if item in ALL_MODES]
    if mode in modes:
        if len(modes) == 1: return True, modes
        modes.remove(mode)
    else:
        modes.append(mode)
    user["modes"] = modes; user["mode"] = modes[0]
    await save_db(db)
    return True, modes
async def update_discord(tg, tag): db=await load_db(); db["users"].get(str(tg), {}).update(discord_tag=sanitize_input(tag,50)); await save_db(db)
async def get_user_stats(tg): return (await load_db())["stats"].get(str(tg),{"bans":0,"mutes":0,"checks":0})
async def ban_user(tg):
    admin_ids = {int(item.strip()) for item in os.getenv("ADMIN_IDS", "").split(",") if item.strip().isdigit()}
    if int(tg) in admin_ids: return False
    db=await load_db(); db["users"].get(str(tg), {}).update(is_banned=1); await save_db(db); return True
async def kick_user(tg): db=await load_db(); db["users"].pop(str(tg),None); await save_db(db)

async def create_application(user_id, app_type, comment=""):
    db=await load_db(); app_id=max([int(a.get("id",0)) for a in db["applications"]],default=0)+1; db["applications"].append({"id":app_id,"user_id":user_id,"type":app_type if app_type in ("entry_request","key_request") else "entry_request","comment":sanitize_input(comment,500),"status":"pending"}); await save_db(db); return app_id
async def get_pending_applications(): return [a for a in (await load_db())["applications"] if a.get("status")=="pending"]
async def update_app_status(app_id,status):
    db=await load_db();
    for a in db["applications"]:
        if a.get("id")==app_id: a["status"]=status
    await save_db(db)

async def create_login_request(owner_id: int, code: str, ip: str, location: str) -> str:
    db = await load_db()
    request_id = uuid.uuid4().hex
    db.setdefault("login_requests", {})[request_id] = {"id": request_id, "owner_id": owner_id, "key_code": code,
        "ip": ip, "location": location, "status": "pending", "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    await save_db(db)
    return request_id

async def get_login_request(request_id: str) -> dict | None:
    return (await load_db()).get("login_requests", {}).get(request_id)

async def update_login_request(request_id: str, status: str) -> bool:
    db = await load_db(); item = db.get("login_requests", {}).get(request_id)
    if not item: return False
    item["status"] = status; item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await save_db(db); return True

async def set_client_kicked(owner_id: int, kicked: bool) -> None:
    db = await load_db(); user = db.get("users", {}).get(str(owner_id))
    if user:
        user["client_kicked"] = bool(kicked)
        await save_db(db)

async def update_user_setting(owner_id: int, name: str, value: bool) -> None:
    db = await load_db(); user = db.get("users", {}).get(str(owner_id))
    if user:
        user.setdefault("settings", {})[name] = bool(value)
        await save_db(db)

async def record_moderation_event(owner_id: int, event_type: str, target: str, action: str, duration: str, reason: str) -> None:
    db = await load_db(); user_id = str(owner_id)
    bucket = "punishment_logs" if event_type == "punishment" else "check_logs"
    db.setdefault(bucket, {}).setdefault(user_id, []).append({"target": target, "action": action, "duration": duration, "reason": reason, "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    stats = db["stats"].setdefault(user_id, {"user_id": owner_id, "bans": 0, "mutes": 0, "checks": 0})
    if event_type == "punishment": stats["mutes" if action == "Мут" else "bans"] = stats.get("mutes" if action == "Мут" else "bans", 0) + 1
    else: stats["checks"] = stats.get("checks", 0) + 1
    await save_db(db)

async def add_irc_message(owner_id: int, nickname: str, role: str, text: str, is_admin: bool = False, recipient_id: int | None = None, title: str = "") -> int:
    db = await load_db(); messages = db.setdefault("irc_messages", [])
    message_id = max([int(item.get("id", 0)) for item in messages], default=0) + 1
    messages.append({"id": message_id, "owner_id": owner_id, "recipient_id": recipient_id, "nickname": nickname, "role": role, "is_admin": is_admin, "title": sanitize_input(title, 80), "text": sanitize_input(text, 500), "created_at": datetime.now().strftime("%H:%M:%S")})
    db["irc_messages"] = messages[-500:]; await save_db(db); return message_id

async def get_irc_messages(after_id: int = 0, recipient_id: int | None = None) -> list[dict]:
    return [item for item in (await load_db()).get("irc_messages", []) if int(item.get("id", 0)) > after_id and (item.get("recipient_id") is None or item.get("recipient_id") == recipient_id or item.get("owner_id") == recipient_id)]

async def set_meme_effect(owner_id: int, scenario: str, target: str, started_at: int) -> None:
    db = await load_db(); db.setdefault("meme_effects", {})[str(owner_id)] = {"owner_id": owner_id, "scenario": scenario, "target": target, "started_at": started_at, "updated_at": datetime.now().timestamp()}; await save_db(db)

async def remove_meme_effect(owner_id: int) -> None:
    db = await load_db(); db.setdefault("meme_effects", {}).pop(str(owner_id), None); await save_db(db)

async def set_irc_mute(owner_id: int, target: str, duration: str, reason: str) -> None:
    db = await load_db(); db.setdefault("irc_mutes", {})[target.casefold()] = {"target": target, "duration": duration, "reason": reason, "owner_id": owner_id, "created_at": datetime.now().timestamp()}; await save_db(db)

async def is_irc_muted(target: str) -> bool:
    item = (await load_db()).get("irc_mutes", {}).get(target.casefold())
    if not item: return False
    raw = item.get("duration", "0")
    try:
        value, unit = int(raw[:-1]), raw[-1].lower(); seconds = value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
        if datetime.now().timestamp() - float(item.get("created_at", 0)) >= seconds:
            return False
    except (ValueError, KeyError):
        return True
    return True

IRC_TITLES = [
"&#FFD4D4A&#FFB6B6n&#FF9898y&#FF7B7BD&#FF5D5De&#FF3F3Fs&#FF2121k",
"&#5CB0FFᴘ&#50A5FFу&#449AFFч&#398EFFн&#2D83FFи&#2178FFᴋ",
"&#BB5CFF/&#CC4DF2s&#DD3FE6p&#EE30D9e&#FF21CCc",
"&#5CFF74ф&#5AFF6Cр&#58FF63и &#54FF53м&#52FF4Bо&#50FF42д&#4EFF3Aе&#4CFF32р&#4AFF29к&#48FF21а",
"&#FF5CC6ч&#FF4DBAи&#FF3FAEт&#FF30A2а&#FF2196к",
"&#FF0000/&#EF0000b&#E00000a&#D00000n &#B00000a&#A10000l&#910000l",
"&#FF8600ᴄ&#FF8E0Dʜ&#FF971Aᴇ&#FF9F27ᴀ&#FFA734ᴛ &#FFB84Eʜ&#FFC05Bᴜ&#FFC868ɴ&#FFD075ᴇ&#FFD982ʀ",
"&#FFFFFFд&#E5E5E5ᴘ&#CACACAу&#B0B0B0н",
"&#FF74CEш&#FD70B3и&#FC6D98п&#FA697Eу&#F86563ч&#F76248ᴋ&#F55E2Dᴀ",
"&#5CF2FFᴛ&#55E6FEɪ&#4FD9FCᴇ&#48CDFBʀ &#3AB5F8- &#2D9CF51",
"&#FF5C5Cп&#F67654ᴀ&#ED8F4Cн&#E5A945т&#DCC23Dᴇ&#D3DC35ᴘ&#CAF52Dᴀ",
"&#FFF45Cп&#EAE56Bо&#D5D57Bт&#C0C68Aу&#ABB799ж&#96A8A9н&#8198B8о &#577AD6л&#426AE6ᴇ&#2D5BF5т"
]
async def set_irc_title(user_id: int, title: str) -> bool:
    db = await load_db(); user = db.get("users", {}).get(str(user_id))
    if not user or title not in IRC_TITLES: return False
    user["irc_title"] = title; await save_db(db); return True
async def clear_irc_title(user_id: int) -> None:
    db = await load_db(); user = db.get("users", {}).get(str(user_id), {}); user.pop("irc_title", None); await save_db(db)
async def get_latest_mod():
    mods=(await load_db())["mod_versions"]; return mods[-1] if mods else None
async def get_all_mod_versions(): return list(reversed((await load_db())["mod_versions"]))
async def save_mod_version(version_name,changelog,file_id,roles):
    db=await load_db()
    if any(m.get("version_name", "").casefold() == str(version_name).casefold() for m in db["mod_versions"]): return False
    db["mod_versions"].append({"id":max([int(m.get("id",0)) for m in db["mod_versions"]],default=0)+1,"version_name":version_name,"changelog":changelog,"file_id":file_id,"allowed_roles":list(roles),"created_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S")}); await save_db(db); return True
async def delete_mod_version(mod_id): db=await load_db(); db["mod_versions"]=[m for m in db["mod_versions"] if m.get("id")!=mod_id]; await save_db(db)
async def export_table_to_txt(table_name):
    filename = str(DB_PATH.parent / f"{sanitize_input(table_name,32)}.txt")
    data = (await load_db()).get(table_name, {})
    async with aiofiles.open(filename, "w", encoding="utf-8") as f:
        await f.write(json.dumps(data, ensure_ascii=False, indent=2))
    return filename
