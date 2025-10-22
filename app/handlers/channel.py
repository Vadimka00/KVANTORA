from aiogram import Router
from aiogram.types import Message, ReactionTypeEmoji, ReactionTypeCustomEmoji
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from ..config import config
from ..db import SessionLocal
from ..keyboards import comment_kb
from ..models import Channel
import asyncio, random

router = Router()

def _pick_random_reactions() -> list:
    pool = []
    # обычные Unicode
    for e in (config.auto_reactions or []):
        pool.append(ReactionTypeEmoji(emoji=e))
    # кастомные (премиум) по ID
    for cid in (config.custom_reaction_ids or []):
        pool.append(ReactionTypeCustomEmoji(custom_emoji_id=cid))
    if not pool:
        return []
    k = random.randint(1, min(len(pool), config.reaction_max_count))
    return random.sample(pool, k)

async def _try_set_reactions(bot, chat_id: int, msg_id: int):
    attempts = max(1, config.reaction_attempts)
    last_err = None
    for i in range(attempts):
        reactions = _pick_random_reactions()
        if not reactions:
            return False

        # С вероятностью reaction_big_prob делаем первую реакцию «большой»
        is_big = random.random() < config.reaction_big_prob

        try:
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=msg_id,
                reaction=reactions,
                is_big=is_big,
            )
            return True
        except TelegramBadRequest as e:
            last_err = e
            # Попытка отката: оставить только обычные Unicode-реакции (частая причина — кастомки не разрешены)
            unicode_only = [r for r in reactions if isinstance(r, ReactionTypeEmoji)]
            if unicode_only:
                try:
                    await bot.set_message_reaction(
                        chat_id=chat_id,
                        message_id=msg_id,
                        reaction=unicode_only,
                        is_big=is_big,
                    )
                    return True
                except TelegramBadRequest as e2:
                    last_err = e2
            # Маленькая пауза и новая попытка с другим набором
            await asyncio.sleep(0.5 * (i + 1))
        except Exception as e:
            # Любая другая ошибка — прекращаем
            last_err = e
            break
    if last_err:
        print(f"⚠️ Не удалось поставить реакцию после {attempts} попыток:", last_err)
    return False

@router.channel_post()
async def on_channel_post(msg: Message):
    if msg.chat.id not in config.allowed_channels:
        return

    # Обновляем/создаём запись канала
    async with SessionLocal() as session:
        ch = (await session.execute(select(Channel).where(Channel.chat_id == msg.chat.id))).scalar_one_or_none()
        if not ch:
            ch = Channel(chat_id=msg.chat.id, username=msg.chat.username, title=msg.chat.title)
            session.add(ch)
        else:
            ch.username = msg.chat.username
            ch.title = msg.chat.title
        await session.commit()

    # Вешаем кнопку
    try:
        await msg.bot.edit_message_reply_markup(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reply_markup=comment_kb(msg.chat.id, msg.message_id)
        )
    except TelegramBadRequest:
        pass

    # 🔥 Пытаемся поставить случайные реакции к новому посту
    await _try_set_reactions(msg.bot, msg.chat.id, msg.message_id)