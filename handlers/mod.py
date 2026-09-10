from html import escape
from aiogram import Router, F
from aiogram.types import Message
import database as db

router = Router()

@router.message(F.text == "📦 Мод HF-Moderation")
async def download_mod(message: Message):
    user = await db.get_user(message.from_user.id)
    if not user or not user.get("is_approved") or user.get("is_banned"): return

    mod = await db.get_latest_mod()
    
    if not mod:
        await message.answer("❌ Мод пока еще не загружен в базу!")
        return

    allowed_roles = mod.get("allowed_roles", mod.get("roles", []))
    
    if user["role"] not in allowed_roles and user["role"] not in db.ADMIN_ROLES:
        await message.answer(
            "⛔ <b>Отказ в доступе!</b>\n\n"
            "У вашей роли нет прав для скачивания HF-Moderation.",
            parse_mode="HTML"
        )
        return

    text = (
        f"📦 <b>HF-Moderation {escape(str(mod.get('version_name', '')))}</b>\n\n"
        f"📅 <b>Дата релиза:</b> {escape(str(mod.get('created_at', 'не указана')))}\n"
        f"📝 <b>Список изменений:</b>\n{escape(str(mod.get('changelog', 'не указан')))}\n\n"
        f"⬇️ <i>Файл отправлен ниже:</i>"
    )
    
    await message.answer_document(document=mod["file_id"], caption=text, parse_mode="HTML")
