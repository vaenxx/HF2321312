import os
from dotenv import load_dotenv
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import database as db

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

router = Router()

def get_admin_ids() -> list[int]:
    """Считывает список ID администраторов из .env"""
    raw_ids = os.getenv("ADMIN_IDS", "").split(",")
    return [int(x.strip()) for x in raw_ids if x.strip().isdigit()]

class AuthStates(StatesGroup):
    waiting_for_key = State()

def get_main_reply_kb(user_id: int) -> ReplyKeyboardMarkup:
    """Генерирует клавиатуру. Кнопка 'Админ-панель' добавляется если user_id есть в ADMIN_IDS."""
    keyboard = [
        [KeyboardButton(text="👤 Личный кабинет"), KeyboardButton(text="📦 Мод HF-Moderation")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="🔗 Привязать Discord")],
        [KeyboardButton(text="🔑 Запросить новый ключ")]
    ]
    
    if user_id in get_admin_ids():
        keyboard.append([KeyboardButton(text="👑 Админ-панель")])
        
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

async def verify_user_or_kick(message: Message, state: FSMContext) -> dict | None:
    """Проверяет состояние пользователя в БД при каждом действии."""
    user = await db.get_user(message.from_user.id)
    
    if not user:
        await state.clear()
        await message.answer(
            "🔐 <b>Авторизация в системе HolyFake</b>\n\n"
            "⛔ <i>Доступ ограничен или ваш аккаунт был удален!</i>\n"
            "🔑 Введите ваш Секретный Ключ для входа:",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.set_state(AuthStates.waiting_for_key)
        return None

    if user.get("is_banned"):
        await state.clear()
        await message.answer(
            "🔴 <b>Ваш аккаунт заблокирован Высшей Администрацией.</b>", 
            parse_mode="HTML", 
            reply_markup=ReplyKeyboardRemove()
        )
        return None

    if not user.get("is_approved"):
        await message.answer(
            "⏳ <b>Статус доступа:</b>\n\nВаша заявка находится на рассмотрении.", 
            parse_mode="HTML", 
            reply_markup=ReplyKeyboardRemove()
        )
        return None

    return user

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    user = await verify_user_or_kick(message, state)
    if not user:
        return

    welcome_text = (
        f"👋 <b>Приветствуем, {user['nickname']}!</b>\n\n"
        f"🏷️ <b>Роль:</b> {user['role']}\n"
        f"🎮 <b>Режим:</b> {user['mode']}\n\n"
        f"💡 Используйте нижнее меню для управления."
    )
    await message.answer(
        welcome_text, 
        parse_mode="HTML", 
        reply_markup=get_main_reply_kb(message.from_user.id)
    )

@router.message(AuthStates.waiting_for_key)
async def process_key(message: Message, state: FSMContext, bot):
    key_code = db.sanitize_input(message.text, 128)
    if not key_code:
        await message.answer("❌ Введите ключ текстом.", parse_mode="HTML")
        return
    existing = await db.get_user(message.from_user.id)
    if existing and existing.get("is_approved"):
        await state.clear()
        await message.answer("ℹ️ Ваш доступ уже активирован.", parse_mode="HTML", reply_markup=get_main_reply_kb(message.from_user.id))
        return
    key_data = await db.redeem_key(key_code, message.from_user.id)

    if not key_data:
        await message.answer("❌ <b>Ошибка!</b> Недействительный или уже использованный ключ.", parse_mode="HTML")
        return

    returning_owner = bool(key_data.get("is_used") and key_data.get("used_by") == message.from_user.id)
    await db.create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        nickname=key_data["target_nickname"],
        role=key_data["role"],
        mode=key_data["mode"],
        is_approved=1 if returning_owner else 0,
        days=key_data.get("days", 30),
        key_code=key_code,
    )
    if returning_owner:
        await state.clear()
        await message.answer(
            f"👋 <b>С возвращением, {key_data['target_nickname']}!</b>\n\n"
            f"🎭 <b>Роль:</b> {key_data['role']}\n🎮 <b>Режим:</b> {key_data['mode']}",
            parse_mode="HTML", reply_markup=get_main_reply_kb(message.from_user.id)
        )
        return
    app_id = await db.create_application(message.from_user.id, "entry_request")
    await state.clear()
    
    await message.answer(
        "✅ <b>Заявка сформирована!</b> Ожидайте подтверждения Администрации.", 
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )
    
    admin_ids = get_admin_ids()
    for admin_id in admin_ids:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Принять", callback_data=f"app_accept_{app_id}_{message.from_user.id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"app_reject_{app_id}_{message.from_user.id}")
        ]])
        try:
            await bot.send_message(
                admin_id,
                f"🔔 <b>Новая заявка на вход #{app_id}</b>\n\n"
                f"👤 <b>Ник:</b> <code>{key_data['target_nickname']}</code>\n"
                f"🎭 <b>Роль:</b> {key_data['role']} ({key_data['mode']})\n"
                f"🆔 <b>TG ID:</b> <code>{message.from_user.id}</code>",
                parse_mode="HTML",
                reply_markup=kb
            )
        except Exception:
            pass

@router.callback_query(F.data.startswith("login_approve_"))
async def approve_login(callback):
    request_id = callback.data.removeprefix("login_approve_")
    item = await db.get_login_request(request_id)
    if not item or item.get("owner_id") != callback.from_user.id:
        await callback.answer("Нет доступа к этому запросу.", show_alert=True); return
    await db.update_login_request(request_id, "approved")
    await db.set_client_kicked(item["owner_id"], False)
    await callback.answer("Вход разрешён.", show_alert=True)
    await callback.message.edit_text("✅ <b>Вход в Minecraft разрешён.</b>", parse_mode="HTML")

@router.callback_query(F.data.startswith("login_reject_"))
async def reject_login(callback):
    request_id = callback.data.removeprefix("login_reject_")
    item = await db.get_login_request(request_id)
    if not item or item.get("owner_id") != callback.from_user.id:
        await callback.answer("Нет доступа к этому запросу.", show_alert=True); return
    await db.update_login_request(request_id, "rejected")
    await callback.answer("Вход отклонён.", show_alert=True)
    await callback.message.edit_text("❌ <b>Вход в Minecraft отклонён.</b>", parse_mode="HTML")
