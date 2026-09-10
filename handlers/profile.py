from aiogram import Router, F
from datetime import datetime, timezone
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import database as db
import os

router = Router()

class DiscordStates(StatesGroup):
    waiting_for_tag = State()

class KeyReqStates(StatesGroup):
    waiting_for_reason = State()

def get_cancel_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )

def get_main_reply_kb(user_role: str) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="👤 Личный кабинет"), KeyboardButton(text="📦 Мод HF-Moderation")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="🔗 Привязать Discord")],
        [KeyboardButton(text="🔑 Запросить новый ключ")]
    ]
    if user_role in db.ADMIN_ROLES:
        keyboard.append([KeyboardButton(text="👑 Админ-панель")])
        
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

@router.message(F.text == "❌ Отмена")
async def cancel_action_message(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_user(message.from_user.id)
    role = user["role"] if user else "Стажер"
    await message.answer("❌ <b>Действие отменено.</b>", parse_mode="HTML", reply_markup=get_main_reply_kb(role))

@router.message(F.text == "👤 Мой профиль")
async def show_profile(message: Message):
    user = await db.get_user(message.from_user.id)
    if not user or not user["is_approved"] or user.get("is_banned"): 
        return
    
    discord = user.get("discord_tag") or "❌ Не привязан"
    status = "🟢 Активен" if user["is_approved"] else "⏳ На проверке"
    client_online = False
    last_seen = user.get("client_last_seen")
    if last_seen:
        try:
            seen = datetime.fromisoformat(last_seen)
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            client_online = (datetime.now(timezone.utc) - seen).total_seconds() <= 45
        except ValueError:
            pass
    
    text = (
        f"👤 <b>Личный кабинет</b>\n\n"
        f"📌 <b>Никнейм:</b> <code>{user['nickname']}</code>\n"
        f"🎭 <b>Роль:</b> {user['role']}\n"
        f"🎮 <b>Режим:</b> {user['mode']}\n"
        f"💬 <b>Discord:</b> <code>{discord}</code>\n"
        f"🔑 <b>Код доступа:</b> <code>{user.get('key_code') or 'не привязан'}</code>\n"
        f"🖥️ <b>Клиент Minecraft:</b> {'🟢 В сети' if client_online else '🔴 Не в сети'}\n"
        f"⚡ <b>Статус:</b> {status}\n"
        f"📅 <b>Регистрация:</b> {user['created_at']}"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(F.text == "👤 Личный кабинет")
async def personal_cabinet(message: Message):
    user = await db.get_user(message.from_user.id)
    if not user or not user.get("is_approved") or user.get("is_banned"): return
    await message.answer("👤 <b>Личный кабинет</b>\nВыберите раздел:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Профиль", callback_data="cab_profile"), InlineKeyboardButton(text="🖥 Сессии", callback_data="cab_sessions")],
        [InlineKeyboardButton(text="🔨 Наказания", callback_data="cab_punishments"), InlineKeyboardButton(text="🔍 Проверки", callback_data="cab_checks")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="cab_settings")]
    ]))

@router.callback_query(F.data == "cab_profile")
async def cabinet_profile(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    if not user: return await callback.answer("Профиль не найден", show_alert=True)
    await callback.answer()
    await callback.message.edit_text(f"📄 <b>Профиль</b>\n\n👤 Ник: <code>{user.get('nickname','')}</code>\n🎭 Роль: <b>{user.get('role','')}</b>\n🎮 Режим: {user.get('mode','')}\n🔑 Ключ: <code>{user.get('key_code','не привязан')}</code>\n⏳ Дней: {user.get('days', 0)}", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="cab_home")]]))

@router.callback_query(F.data == "cab_sessions")
async def cabinet_sessions(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    if not user: return await callback.answer("Профиль не найден", show_alert=True)
    last = user.get("client_last_seen") or "нет сигнала"
    await callback.answer()
    await callback.message.edit_text(f"🖥 <b>Активные сессии</b>\n\nMinecraft-клиент: {'🟢 активен' if last != 'нет сигнала' else '🔴 не подключён'}\nПоследний сигнал: <code>{last}</code>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⛔ Кикнуть Minecraft", callback_data="cab_kick")],[InlineKeyboardButton(text="◀️ Назад", callback_data="cab_home")]]))

@router.callback_query(F.data == "cab_kick")
async def cabinet_kick(callback: CallbackQuery):
    await db.set_client_kicked(callback.from_user.id, True)
    await callback.answer("Сессия Minecraft отключена.", show_alert=True)
    await callback.message.edit_text("⛔ <b>Сессия отключена.</b> Клиент потеряет доступ в течение нескольких секунд.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="cab_home")]]))

@router.callback_query(F.data.in_({"cab_punishments", "cab_checks"}))
async def cabinet_stats(callback: CallbackQuery):
    stats = await db.get_user_stats(callback.from_user.id)
    is_checks = callback.data == "cab_checks"
    title = "🔍 Последние проверки" if is_checks else "🔨 Последние наказания"
    value = stats.get("checks", 0) if is_checks else stats.get("bans", 0) + stats.get("mutes", 0)
    await callback.answer()
    await callback.message.edit_text(f"<b>{title}</b>\n\nВсего записей: <b>{value}</b>\n\nЖурнал подробных событий будет пополняться при выполнении действий.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="cab_home")]]))

@router.callback_query(F.data == "cab_settings")
async def cabinet_settings(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id); enabled = user.get("settings", {}).get("punishment_notifications", True) if user else True
    await callback.answer()
    await callback.message.edit_text("⚙️ <b>Настройки личного кабинета</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔔 Уведомления о наказаниях: {'вкл' if enabled else 'выкл'}", callback_data="cab_toggle_notifications")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="cab_home")]]))

@router.callback_query(F.data == "cab_toggle_notifications")
async def cabinet_toggle_notifications(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id); current = user.get("settings", {}).get("punishment_notifications", True) if user else True
    await db.update_user_setting(callback.from_user.id, "punishment_notifications", not current)
    await callback.answer("Настройка обновлена")
    await cabinet_settings(callback)

@router.callback_query(F.data == "cab_home")
async def cabinet_home(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("👤 <b>Личный кабинет</b>\nВыберите раздел:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Профиль", callback_data="cab_profile"), InlineKeyboardButton(text="🖥 Сессии", callback_data="cab_sessions")],
        [InlineKeyboardButton(text="🔨 Наказания", callback_data="cab_punishments"), InlineKeyboardButton(text="🔍 Проверки", callback_data="cab_checks")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="cab_settings")]]))

@router.message(F.text == "📊 Статистика")
async def show_stats(message: Message):
    user = await db.get_user(message.from_user.id)
    if not user or not user["is_approved"] or user.get("is_banned"): 
        return
    
    stats = await db.get_user_stats(message.from_user.id)
    text = (
        f"📊 <b>Ваша статистика:</b>\n\n"
        f"🔨 <b>Забанено:</b> {stats.get('bans', 0)}\n"
        f"😶 <b>Замучено:</b> {stats.get('mutes', 0)}\n"
        f"🔍 <b>Проведено проверок:</b> {stats.get('checks', 0)}"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(F.text == "🔗 Привязать Discord")
async def bind_discord_start(message: Message, state: FSMContext):
    user = await db.get_user(message.from_user.id)
    if not user or not user["is_approved"] or user.get("is_banned"): 
        return

    await message.answer(
        "🔗 <b>Привязка Discord</b>\n\n✏️ Введите ваш Discord Tag или ID (макс. 50 символов):",
        reply_markup=get_cancel_reply_kb(),
        parse_mode="HTML"
    )
    await state.set_state(DiscordStates.waiting_for_tag)

@router.message(DiscordStates.waiting_for_tag)
async def process_discord(message: Message, state: FSMContext):
    tag = db.sanitize_input(message.text, max_length=50)
    user = await db.get_user(message.from_user.id)
    
    if len(tag) < 3:
        await message.answer("❌ <b>Ошибка!</b> Введите корректный Discord Tag (минимум 3 символа).", parse_mode="HTML")
        return

    await db.update_discord(message.from_user.id, tag)
    await state.clear()
    await message.answer("🎉 <b>Успешно!</b> Discord профиль обновлен.", parse_mode="HTML", reply_markup=get_main_reply_kb(user["role"]))

@router.message(F.text == "🔑 Запросить новый ключ")
async def req_key_start(message: Message, state: FSMContext):
    user = await db.get_user(message.from_user.id)
    if not user or not user["is_approved"] or user.get("is_banned"): 
        return

    await message.answer(
        "🔑 <b>Запрос смены ключа</b>\n\n📝 Опишите причину запроса нового ключа:",
        reply_markup=get_cancel_reply_kb(),
        parse_mode="HTML"
    )
    await state.set_state(KeyReqStates.waiting_for_reason)

@router.message(KeyReqStates.waiting_for_reason)
async def process_key_req(message: Message, state: FSMContext, bot):
    reason = db.sanitize_input(message.text, max_length=200)
    user = await db.get_user(message.from_user.id)
    
    app_id = await db.create_application(message.from_user.id, "key_request", reason)
    await state.clear()
    
    await message.answer("🚀 <b>Заявка отправлена!</b> Ожидайте решения Высшей Администрации.", parse_mode="HTML", reply_markup=get_main_reply_kb(user["role"]))
