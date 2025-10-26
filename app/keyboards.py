# keyboards.py
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from .config import config

def _short_count(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        v = n / 1000
        s = f"{v:.1f}"
        if s.endswith(".0"):
            s = s[:-2]
        return f"{s} K"
    v = n / 1_000_000
    s = f"{v:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    return f"{s} M"

def comment_kb(chat_id: int, msg_id: int, count: int | None = None) -> InlineKeyboardMarkup:
    payload = f"{chat_id}msg{msg_id}"
    url = f"https://t.me/{config.bot_username}?start={payload}"

    label = "💬 Комментировать"
    if count and count > 0:
        label = f"{label} ({_short_count(count)})"

    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, url=url)]]
    )