from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from .config import config

def comment_kb(chat_id: int, msg_id: int) -> InlineKeyboardMarkup:
    # простая payload без шифрования
    payload = f"{chat_id}msg{msg_id}"
    url = f"https://t.me/{config.bot_username}?start={payload}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Комментировать", url=url)]
        ]
    )