
# utils.py
from sqlalchemy import select, func
from aiogram.exceptions import TelegramBadRequest
from .db import SessionLocal
from .models import Comment
from .keyboards import comment_kb

async def refresh_comment_button(bot, channel_chat_id: int, post_id: int):
    # Считаем количество комментариев и переустанавливаем разметку с новым лейблом
    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count(Comment.id)).where(
                Comment.channel_chat_id == channel_chat_id,
                Comment.post_id == post_id
            )
        )
    try:
        await bot.edit_message_reply_markup(
            chat_id=channel_chat_id,
            message_id=post_id,
            reply_markup=comment_kb(channel_chat_id, post_id, count=count or 0)
        )
    except TelegramBadRequest:
        # нет прав/нельзя редактировать — молча игнорим
        pass
    
def build_post_link(chat_id: int, username: str | None, message_id: int) -> str | None:
    if username:
        return f"https://t.me/{username}/{message_id}"
    # приватный: chat_id вида -1001234567890 -> internal 1234567890
    internal = str(abs(chat_id))
    if internal.startswith("100"):
        internal = internal[3:]
    return f"https://t.me/c/{internal}/{message_id}"