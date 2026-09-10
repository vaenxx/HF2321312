import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, KeyboardButton, FSInputFile, ReplyKeyboardRemove
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import database as db

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

router = Router()

def get_admin_ids() -> list[int]:
    """Считывает список ID администраторов из .env"""
    raw_ids = os.getenv("ADMIN_IDS", "").split(",")
    return [int(x.strip()) for x in raw_ids if x.strip().isdigit()]

class KeyGenStates(StatesGroup):
    waiting_for_nick = State()
    waiting_for_days = State()

class ModUploadStates(StatesGroup):
    waiting_for_file = State()
    waiting_for_version = State()
    waiting_for_changelog = State()
    waiting_for_roles = State()

class BroadcastStates(StatesGroup):
    waiting_for_message = State()

def get_admin_main_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👥 Список модераторов"), KeyboardButton(text="🔑 База ключей")],
        [KeyboardButton(text="📥 Заявки на ключи"), KeyboardButton(text="➕ Создать новый ключ")],
        [KeyboardButton(text="📦 Управление модом"), KeyboardButton(text="📚 Версии мода")],
        [KeyboardButton(text="📄 Выгрузить базы в TXT")],
        [KeyboardButton(text="📢 Глобальное сообщение")],
        [KeyboardButton(text="◀️ Главное меню")]
    ], resize_keyboard=True)

def get_cancel_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)

async def check_admin_access(message: Message) -> bool:
    """Проверяет админ-права по ADMIN_IDS из .env."""
    if message.from_user.id not in get_admin_ids():
        await message.answer(
            "⛔ <b>У вас нет доступа к этому разделу!</b>", 
            parse_mode="HTML", 
            reply_markup=ReplyKeyboardRemove()
        )
        return False
    return True

# --- ОБРАБОТКА ОТМЕНЫ СОСТОЯНИЙ ---
@router.message(F.text == "❌ Отмена")
async def cancel_handler(message: Message, state: FSMContext):
    if not await check_admin_access(message): return
    
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
        
    await message.answer(
        "❌ <b>Действие отменено.</b>", 
        parse_mode="HTML", 
        reply_markup=get_admin_main_reply_kb()
    )

# --- ГЛАВНОЕ МЕНЮ И НАВИГАЦИЯ ---
@router.message(F.text == "◀️ Главное меню")
async def back_to_main_menu(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer("⛔ <b>У вас нет доступа!</b>", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        return
        
    from handlers.auth import get_main_reply_kb
    await message.answer("🏠 <b>Вы вернулись в главное меню.</b>", parse_mode="HTML", reply_markup=get_main_reply_kb(message.from_user.id))

@router.message(F.text == "👑 Админ-панель")
async def admin_panel_main(message: Message):
    if not await check_admin_access(message): return

    await message.answer(
        "👑 <b>Панель Высшей Администрации HolyFake</b>\n\nВыберите нужный раздел в меню ниже:",
        parse_mode="HTML",
        reply_markup=get_admin_main_reply_kb()
    )

# --- 1. ТАБЛИЦА БАЗЫ МОДЕРАТОРОВ ---
@router.message(F.text == "👥 Список модераторов")
async def view_mods_table(message: Message):
    if not await check_admin_access(message): return

    users = await db.get_all_users(limit=15, offset=0)
    if not users:
        await message.answer("👥 <b>База модераторов пуста.</b>", parse_mode="HTML")
        return

    text = "👥 <b>База модераторов (Таблица):</b>\n\n"
    kb_rows = []

    for idx, u in enumerate(users, start=1):
        status_icon = "🔴" if u.get("is_banned") else ("🟢" if u.get("is_approved") else "⏳")
        client_icon = "🔌"
        last_seen = u.get("client_last_seen")
        if last_seen:
            try:
                seen = datetime.fromisoformat(last_seen)
                if seen.tzinfo is None: seen = seen.replace(tzinfo=timezone.utc)
                client_icon = "🟢" if (datetime.now(timezone.utc) - seen).total_seconds() <= 45 else "🔴"
            except ValueError:
                pass
        username = f"@{u['username']}" if u.get("username") else "без username"
        text += f"{idx}. {status_icon} <b>{u['nickname']}</b> | Роль: <code>{u['role']}</code> | Minecraft: {client_icon}\n"
        text += f"   └ {username} | TG ID: <code>{u['telegram_id']}</code>\n"
        
        kb_rows.append([
            InlineKeyboardButton(text=f"🔨 Забанить {u['nickname']}", callback_data=f"ban_usr_{u['telegram_id']}"),
            InlineKeyboardButton(text=f"❌ Кикнуть", callback_data=f"kick_usr_{u['telegram_id']}"),
        ])
        kb_rows.append([
            InlineKeyboardButton(text="🎭 Изменить ранг", callback_data=f"role_usr_{u['telegram_id']}"),
            InlineKeyboardButton(text="🎮 Режимы", callback_data=f"mode_usr_{u['telegram_id']}")
        ])

    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

@router.callback_query(F.data.startswith("ban_usr_"))
async def process_ban_usr(callback: CallbackQuery):
    if callback.from_user.id not in get_admin_ids():
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    target_tg = int(callback.data.split("_")[2])
    if target_tg in get_admin_ids():
        await callback.answer("⛔ Администратора нельзя забанить.", show_alert=True)
        return
    await db.ban_user(target_tg)
    await callback.answer("🔒 Пользователь забанен!", show_alert=True)
    await callback.message.delete()

@router.callback_query(F.data.startswith("kick_usr_"))
async def process_kick_usr(callback: CallbackQuery):
    if callback.from_user.id not in get_admin_ids():
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    target_tg = int(callback.data.split("_")[2])
    await db.kick_user(target_tg)
    await callback.answer("❌ Пользователь удален из базы!", show_alert=True)
    await callback.message.delete()

# --- 2. ТАБЛИЦА БАЗЫ КЛЮЧЕЙ ---
@router.message(F.text == "🔑 База ключей")
async def view_keys_table(message: Message):
    if not await check_admin_access(message): return

    keys = await db.get_all_keys(limit=15, offset=0)
    if not keys:
        await message.answer("🔑 <b>База ключей пуста.</b>", parse_mode="HTML")
        return

    text = "🔑 <b>Единая база ключей (Таблица):</b>\n\n"
    kb_rows = []

    for idx, k in enumerate(keys, start=1):
        if isinstance(k, dict):
            key_code = k.get("key_code") or k.get("key") or k.get("code") or "N/A"
            target_nick = k.get("target_nickname") or k.get("nickname") or "Неизвестно"
            is_used = k.get("is_used", False)
        else:
            key_code = str(k[0]) if len(k) > 0 else "N/A"
            target_nick = str(k[1]) if len(k) > 1 else "Неизвестно"
            is_used = bool(k[2]) if len(k) > 2 else False

        status = "🔴 Использован" if is_used else "🟢 Активен"
        short_key = key_code[:16] if key_code != "N/A" else "none"
        
        text += f"{idx}. <b>{target_nick}</b> — <code>{key_code}</code> ({status})\n"
        kb_rows.append([
            InlineKeyboardButton(
                text=f"🗑️ Удалить ключ {target_nick}", 
                callback_data=f"del_k_{short_key}"
            ),
            InlineKeyboardButton(
                text="🔓 Сбросить привязку",
                callback_data=f"reset_k_{short_key}"
            )
        ])

    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    
@router.callback_query(F.data.startswith("del_k_"))
async def process_del_key(callback: CallbackQuery):
    if callback.from_user.id not in get_admin_ids():
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    short_key = callback.data.replace("del_k_", "")
    full_key = await db.find_key_by_prefix(short_key)
    
    if full_key:
        await db.delete_key(full_key)
        await callback.answer("🗑️ Ключ удален из базы!", show_alert=True)
    else:
        await callback.answer("❌ Ключ не найден или уже был удален.", show_alert=True)
        
    await callback.message.delete()

@router.callback_query(F.data.startswith("role_usr_"))
async def process_role_usr(callback: CallbackQuery):
    if callback.from_user.id not in get_admin_ids():
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    try:
        target_tg = int(callback.data.removeprefix("role_usr_"))
    except ValueError:
        await callback.answer("Некорректный пользователь.", show_alert=True)
        return
    user = await db.get_user(target_tg)
    if not user:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return
    rows = [[InlineKeyboardButton(text=f"🎭 {role}", callback_data=f"role_set_{target_tg}_{index}")]
            for index, role in enumerate(db.ALL_ROLES)]
    rows.append([InlineKeyboardButton(text="◀️ Отмена", callback_data="role_cancel")])
    await callback.answer()
    await callback.message.edit_text(
        f"🎭 <b>Выберите ранг</b>\n\n👤 {user.get('nickname', target_tg)}\n"
        f"TG ID: <code>{target_tg}</code>", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )

@router.callback_query(F.data.startswith("role_set_"))
async def process_role_set(callback: CallbackQuery):
    if callback.from_user.id not in get_admin_ids():
        await callback.answer("⛔ Нет доступа!", show_alert=True); return
    parts = callback.data.split("_")
    try:
        target_tg, role_index = int(parts[2]), int(parts[3])
        new_role = db.ALL_ROLES[role_index]
    except (ValueError, IndexError):
        await callback.answer("Некорректный ранг.", show_alert=True); return
    if not await db.set_user_role(target_tg, new_role):
        await callback.answer("Пользователь не найден.", show_alert=True); return
    await callback.answer(f"Ранг изменён: {new_role}", show_alert=True)
    await callback.message.edit_text(f"✅ <b>Ранг изменён</b>\n\n🎭 Новый ранг: <code>{new_role}</code>", parse_mode="HTML")

@router.callback_query(F.data == "role_cancel")
async def process_role_cancel(callback: CallbackQuery):
    await callback.answer("Отменено")
    await callback.message.delete()

@router.callback_query(F.data.startswith("mode_usr_"))
async def process_mode_usr(callback: CallbackQuery):
    if callback.from_user.id not in get_admin_ids():
        await callback.answer("⛔ Нет доступа!", show_alert=True); return
    try: target_tg = int(callback.data.removeprefix("mode_usr_"))
    except ValueError:
        await callback.answer("Некорректный пользователь.", show_alert=True); return
    user = await db.get_user(target_tg)
    if not user:
        await callback.answer("Пользователь не найден.", show_alert=True); return
    modes = set(user.get("modes", [user.get("mode", "HolyWorld")]))
    rows = [[InlineKeyboardButton(text=("✅ " if mode in modes else "⬜ ") + mode, callback_data=f"mode_set_{target_tg}_{index}")]
            for index, mode in enumerate(db.ALL_MODES)]
    rows.append([InlineKeyboardButton(text="◀️ Отмена", callback_data="mode_cancel")])
    await callback.answer()
    await callback.message.edit_text(f"🎮 <b>Режимы модератора</b>\n\n👤 {user.get('nickname', target_tg)}\nМожно выбрать несколько режимов.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data.startswith("mode_set_"))
async def process_mode_set(callback: CallbackQuery):
    if callback.from_user.id not in get_admin_ids():
        await callback.answer("⛔ Нет доступа!", show_alert=True); return
    parts = callback.data.split("_")
    try: target_tg, index = int(parts[2]), int(parts[3]); mode = db.ALL_MODES[index]
    except (ValueError, IndexError):
        await callback.answer("Некорректный режим.", show_alert=True); return
    ok, modes = await db.toggle_user_mode(target_tg, mode)
    if not ok:
        await callback.answer("Пользователь не найден.", show_alert=True); return
    await callback.answer("Режимы обновлены")
    rows = [[InlineKeyboardButton(text=("✅ " if item in modes else "⬜ ") + item, callback_data=f"mode_set_{target_tg}_{i}")]
            for i, item in enumerate(db.ALL_MODES)]
    rows.append([InlineKeyboardButton(text="◀️ Закрыть", callback_data="mode_cancel")])
    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data == "mode_cancel")
async def process_mode_cancel(callback: CallbackQuery):
    await callback.answer("Готово")
    await callback.message.delete()

@router.callback_query(F.data.startswith("reset_k_"))
async def process_reset_key(callback: CallbackQuery):
    if callback.from_user.id not in get_admin_ids():
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    prefix = callback.data.replace("reset_k_", "")
    full_key = await db.find_key_by_prefix(prefix)
    if not full_key or not await db.reset_key_binding(full_key):
        await callback.answer("❌ Ключ не найден.", show_alert=True)
        return
    await callback.answer("🔓 Привязка ключа сброшена.", show_alert=True)

# --- 3. ЕДИНАЯ ТАБЛИЦА ЗАЯВОК (Вход и Смена ключа) ---
@router.message(F.text == "📥 Заявки на ключи")
@router.message(F.text == "📥 Все заявки")
async def view_apps_table(message: Message):
    if not await check_admin_access(message): return

    apps = await db.get_pending_applications()
    if not apps:
        await message.answer("📥 <b>Активных заявок нет.</b>", parse_mode="HTML")
        return

    text = "📥 <b>Единая база заявок (Вход и Смена ключа):</b>\n\n"
    kb_rows = []

    for idx, app in enumerate(apps, start=1):
        user_id = app["user_id"]
        app_user = await db.get_user(user_id)
        
        # Различаем тип заявки
        app_type = app.get("type", "entry_request")
        type_tag = "🔑 [Смена ключа]" if app_type == "key_request" else "🚪 [Заявка на вход]"
        
        nick = app_user["nickname"] if app_user else f"ID {user_id}"
        comment_str = f"\n   ├ 💬 <i>{app['comment']}</i>" if app.get("comment") else ""
        
        text += f"{idx}. {type_tag} <b>{nick}</b>{comment_str}\n"
        
        kb_rows.append([
            InlineKeyboardButton(text=f"✅ Принять ({nick})", callback_data=f"app_accept_{app['id']}_{user_id}"),
            InlineKeyboardButton(text=f"❌ Отклонить", callback_data=f"app_reject_{app['id']}_{user_id}")
        ])

    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

@router.callback_query(F.data.startswith("app_accept_"))
@router.callback_query(F.data.startswith("key_accept_"))
async def process_accept_app(callback: CallbackQuery, bot):
    if callback.from_user.id not in get_admin_ids():
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    parts = callback.data.split("_")
    app_id = int(parts[2])
    user_id = int(parts[3])
    
    user = await db.get_user(user_id)
    if not user:
        await callback.answer("❌ Пользователь не найден!", show_alert=True)
        return

    app = next((item for item in (await db.load_db()).get("applications", []) if item.get("id") == app_id), None)
    await db.approve_user(user_id)
    new_key = user.get("key_code", "")
    days = user.get("days", 30)
    if app and app.get("type") == "key_request":
        new_key = await db.create_key(user["nickname"], user["role"], user["mode"], 30)
        days = 30
        await db.set_user_key(user_id, new_key, days)

    await db.update_app_status(app_id, "approved")
    
    try:
        await bot.send_message(
            user_id,
            f"🎉 <b>Ваша заявка одобрена Администрацией!</b>\n\n"
            f"🔑 <b>Ваш активный ключ:</b>\n<code>{new_key}</code>\n\n"
            f"⏳ <b>Срок действия:</b> {days} дней.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.answer("✅ Заявка одобрена, новый ключ выдан!", show_alert=True)
    await callback.message.delete()

@router.callback_query(F.data.startswith("app_reject_"))
@router.callback_query(F.data.startswith("key_reject_"))
async def process_reject_app(callback: CallbackQuery, bot):
    if callback.from_user.id not in get_admin_ids():
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    parts = callback.data.split("_")
    app_id = int(parts[2])
    user_id = int(parts[3]) if len(parts) > 3 else None
    
    await db.update_app_status(app_id, "rejected")
    
    if user_id:
        try:
            await bot.send_message(
                user_id,
                "❌ <b>Ваша заявка была отклонена Администрацией.</b>",
                parse_mode="HTML"
            )
        except Exception:
            pass

    await callback.answer("❌ Заявка отклонена.", show_alert=True)
    await callback.message.delete()

# --- ГЕНЕРАЦИЯ КЛЮЧА ---
@router.message(F.text == "➕ Создать новый ключ")
async def gen_key_start(message: Message, state: FSMContext):
    if not await check_admin_access(message): return

    await message.answer("🔑 <b>Генератор ключей</b>\n\n✏️ Введите никнейм модератора:", reply_markup=get_cancel_reply_kb(), parse_mode="HTML")
    await state.set_state(KeyGenStates.waiting_for_nick)

@router.message(KeyGenStates.waiting_for_nick)
async def gen_key_nick(message: Message, state: FSMContext):
    nick = db.sanitize_input(message.text, max_length=32)
    await state.update_data(nick=nick)
    await message.answer("⏳ Укажите срок действия ключа в днях (число):", parse_mode="HTML", reply_markup=get_cancel_reply_kb())
    await state.set_state(KeyGenStates.waiting_for_days)

@router.message(KeyGenStates.waiting_for_days)
async def gen_key_days(message: Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) <= 0 or int(message.text) > 3650:
        await message.answer("❌ <b>Ошибка!</b> Введите число дней (от 1 до 3650).", parse_mode="HTML")
        return
    
    data = await state.get_data()
    key_code = await db.create_key(target_nickname=data["nick"], role="Стажер", mode="HolyWorld", days=int(message.text))
    await state.clear()
    
    await message.answer(
        f"🎉 <b>Секретный ключ создан!</b>\n\n🔑 <b>Ключ:</b>\n<code>{key_code}</code>\n\n👤 <b>Для:</b> <code>{data['nick']}</code>\n⏳ <b>Срок:</b> {message.text} дней",
        parse_mode="HTML",
        reply_markup=get_admin_main_reply_kb()
    )

# --- ЭКСПОРТ В TXT ---
@router.message(F.text == "📄 Выгрузить базы в TXT")
async def export_txt_start(message: Message):
    if not await check_admin_access(message): return

    for table in ["users", "keys", "applications"]:
        filename = await db.export_table_to_txt(table)
        file = FSInputFile(filename)
        await message.answer_document(document=file, caption=f"📥 База <code>{table}.txt</code>", parse_mode="HTML")
        if os.path.exists(filename):
            os.remove(filename)

# --- УПРАВЛЕНИЕ МОДОМ ---
@router.message(F.text == "📦 Управление модом")
async def admin_mod_mgmt(message: Message):
    if not await check_admin_access(message): return

    mod = await db.get_latest_mod()
    status = f"Активная версия: <b>{mod['version_name']}</b>" if mod else "Мод еще не загружен."
    
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="⬆️ Загрузить версию мода")],
        [KeyboardButton(text="🗑️ Удалить текущую версию")],
        [KeyboardButton(text="👑 Админ-панель")]
    ], resize_keyboard=True)

    await message.answer(f"📦 <b>Управление HF-Moderation</b>\n\n{status}", parse_mode="HTML", reply_markup=kb)

@router.message(F.text == "🗑️ Удалить текущую версию")
async def process_del_mod(message: Message):
    if not await check_admin_access(message): return

    mod = await db.get_latest_mod()
    if mod:
        await db.delete_mod_version(mod["id"])
        await message.answer("🗑️ <b>Версия мода была удалена!</b>", parse_mode="HTML", reply_markup=get_admin_main_reply_kb())
    else:
        await message.answer("❌ Активная версия мода не найдена.", parse_mode="HTML", reply_markup=get_admin_main_reply_kb())

@router.message(F.text == "⬆️ Загрузить версию мода")
async def upload_mod_start(message: Message, state: FSMContext):
    if not await check_admin_access(message): return

    await message.answer("📦 <b>Загрузка мода</b>\n\n📂 Отправьте файл <code>.jar</code> или <code>.zip</code> (до 50 МБ):", reply_markup=get_cancel_reply_kb(), parse_mode="HTML")
    await state.set_state(ModUploadStates.waiting_for_file)

@router.message(ModUploadStates.waiting_for_file, F.document)
async def upload_mod_file(message: Message, state: FSMContext):
    file_name = message.document.file_name.lower()
    file_size = message.document.file_size
    
    if not (file_name.endswith('.jar') or file_name.endswith('.zip')):
        await message.answer("❌ <b>Разрешены только файлы .jar и .zip!</b>", parse_mode="HTML")
        return
        
    if file_size > 50 * 1024 * 1024:
        await message.answer("❌ <b>Размер файла не должен превышать 50 МБ.</b>", parse_mode="HTML")
        return

    await state.update_data(file_id=message.document.file_id)
    await message.answer("🏷️ Введите имя версии (например: <code>v1.3.0</code>):", parse_mode="HTML", reply_markup=get_cancel_reply_kb())
    await state.set_state(ModUploadStates.waiting_for_version)

@router.message(ModUploadStates.waiting_for_version)
async def upload_mod_version(message: Message, state: FSMContext):
    version = db.sanitize_input(message.text, max_length=20)
    await state.update_data(version=version)
    await message.answer("📝 Введите Чейнджлог (список изменений):", parse_mode="HTML", reply_markup=get_cancel_reply_kb())
    await state.set_state(ModUploadStates.waiting_for_changelog)

@router.message(ModUploadStates.waiting_for_changelog)
async def upload_mod_changelog(message: Message, state: FSMContext):
    changelog = db.sanitize_input(message.text, max_length=1000)
    data = await state.get_data()
    
    await state.update_data(changelog=changelog, selected_roles=[])
    rows = [[InlineKeyboardButton(text=f"⬜ {role}", callback_data=f"mod_role_{index}")] for index, role in enumerate(db.ALL_ROLES)]
    rows.append([InlineKeyboardButton(text="✅ Опубликовать для выбранных", callback_data="mod_publish")])
    await message.answer("🔐 <b>Выберите, кому доступна загрузка этой версии:</b>\nМожно выбрать несколько ролей.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await state.set_state(ModUploadStates.waiting_for_roles)

@router.callback_query(F.data.startswith("mod_role_"), ModUploadStates.waiting_for_roles)
async def toggle_mod_role(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in get_admin_ids(): return await callback.answer("⛔ Нет доступа!", show_alert=True)
    try: index = int(callback.data.removeprefix("mod_role_")); role = db.ALL_ROLES[index]
    except (ValueError, IndexError): return await callback.answer("Некорректная роль.", show_alert=True)
    data = await state.get_data(); roles = list(data.get("selected_roles", []))
    if role in roles: roles.remove(role)
    else: roles.append(role)
    await state.update_data(selected_roles=roles)
    rows = [[InlineKeyboardButton(text=("✅ " if item in roles else "⬜ ") + item, callback_data=f"mod_role_{i}")] for i, item in enumerate(db.ALL_ROLES)]
    rows.append([InlineKeyboardButton(text="✅ Опубликовать для выбранных", callback_data="mod_publish")])
    await callback.answer("Обновлено")
    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data == "mod_publish", ModUploadStates.waiting_for_roles)
async def publish_mod(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in get_admin_ids(): return await callback.answer("⛔ Нет доступа!", show_alert=True)
    data = await state.get_data(); roles = data.get("selected_roles", [])
    if not roles: return await callback.answer("Выберите хотя бы одну роль.", show_alert=True)
    if not await db.save_mod_version(data["version"], data["changelog"], data["file_id"], roles):
        await callback.answer("Такая версия уже существует.", show_alert=True); return
    await state.clear(); await callback.answer("Мод опубликован", show_alert=True)
    await callback.message.edit_text("🎉 <b>Версия мода опубликована.</b>", parse_mode="HTML")

@router.message(F.text == "📚 Версии мода")
async def list_mod_versions(message: Message):
    if not await check_admin_access(message): return
    versions = await db.get_all_mod_versions()
    if not versions:
        await message.answer("📚 <b>Версий мода пока нет.</b>", parse_mode="HTML"); return
    rows = []
    text = "📚 <b>Версии HF-Moderation</b>\n\n"
    for version in versions:
        text += f"• <b>{version.get('version_name','')}</b> | {version.get('created_at','')}\n"
        rows.append([InlineKeyboardButton(text=f"📄 {version.get('version_name','')}", callback_data=f"mod_view_{version.get('id')}")])
    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data.startswith("mod_view_"))
async def view_mod_version(callback: CallbackQuery):
    if callback.from_user.id not in get_admin_ids(): return await callback.answer("⛔ Нет доступа!", show_alert=True)
    try: version_id = int(callback.data.removeprefix("mod_view_"))
    except ValueError: return await callback.answer("Некорректная версия.", show_alert=True)
    versions = await db.get_all_mod_versions(); version = next((item for item in versions if item.get("id") == version_id), None)
    if not version: return await callback.answer("Версия не найдена.", show_alert=True)
    roles = ", ".join(version.get("allowed_roles", version.get("roles", [])))
    await callback.answer()
    await callback.message.edit_text(f"📦 <b>HF-Moderation {version.get('version_name','')}</b>\n\n📅 Создана: {version.get('created_at','')}\n🔐 Роли: {roles}\n\n📝 <b>Чейнджлог:</b>\n{version.get('changelog','—')}", parse_mode="HTML")

@router.message(F.text == "📢 Глобальное сообщение")
async def broadcast_start(message: Message, state: FSMContext):
    if not await check_admin_access(message): return
    await message.answer("📢 <b>Глобальное сообщение</b>\nОтправьте текст, фото или файл с подписью. Сообщение получат все одобренные пользователи.", parse_mode="HTML", reply_markup=get_cancel_reply_kb())
    await state.set_state(BroadcastStates.waiting_for_message)

@router.message(BroadcastStates.waiting_for_message)
async def broadcast_send(message: Message, state: FSMContext, bot):
    if not await check_admin_access(message): return
    users = await db.get_all_users(limit=100000, offset=0); sent = 0
    for user in users:
        if not user.get("is_approved") or user.get("is_banned"): continue
        try:
            await bot.copy_message(chat_id=user["telegram_id"], from_chat_id=message.chat.id, message_id=message.message_id)
            sent += 1
        except Exception:
            continue
    await state.clear()
    await message.answer(f"✅ <b>Глобальное сообщение отправлено.</b> Получателей: {sent}", parse_mode="HTML", reply_markup=get_admin_main_reply_kb())
