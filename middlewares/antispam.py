import time
import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

class AntiSpamMiddleware(BaseMiddleware):
    def __init__(self, limit: float = 0.7):
        """
        :param limit: Минимальный интервал между сообщениями/кликами в секундах
        """
        self.limit = limit
        self.user_timestamps: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user_id = None

        if isinstance(event, Message):
            user_id = event.from_user.id
            raw_text = (event.text or "").strip()
            safe_text = raw_text
            if raw_text.lower().startswith("hf-") or raw_text.lower().startswith(".code"):
                safe_text = ".code <hidden>"
            logging.info("[AUDIT] message user_id=%s username=%s text=%s", user_id, event.from_user.username or "-", safe_text[:120])
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            logging.info("[AUDIT] callback user_id=%s username=%s data=%s", user_id, event.from_user.username or "-", event.data or "-")

        if user_id:
            current_time = time.time()
            last_time = self.user_timestamps.get(user_id, 0)

            # Проверка частоты запросов
            if current_time - last_time < self.limit:
                if isinstance(event, CallbackQuery):
                    await event.answer("⚠️ Не спамьте! Подождите секунду.", show_alert=True)
                elif isinstance(event, Message):
                    await event.answer("⚠️ <b>Антиспам:</b> Пожалуйста, не отправляйте сообщения так часто!", parse_mode="HTML")
                return  # Блокируем дальнейшее выполнение

            self.user_timestamps[user_id] = current_time

        return await handler(event, data)
