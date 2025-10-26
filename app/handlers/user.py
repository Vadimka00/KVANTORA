from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.types import Message, InputMediaPhoto, InputMediaVideo, InputMediaDocument
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
import html, re, asyncio
from collections import defaultdict

from ..config import config
from ..db import SessionLocal
from ..antispam import check_and_hit
from ..models import User, Comment, CommentMedia, Channel
from ..utils import build_post_link, refresh_comment_button


router = Router()
print("✅ handlers/user.py подключён")

# user_id -> (channel_chat_id, post_id)
_pending: dict[int, tuple[int, int]] = {}

BOT_SIGNATURE = f'<a href="https://t.me/{config.bot_username}">KVANTORA™</a>'

# ---------- Парсер меток из текста/подписи ----------
_CTX_RE = re.compile(r"UID:(-?\d+)\b.*?CID:(-?\d+)\b.*?PID:(\d+)\b(?:.*?AMID:(\d+))?", re.S)
def _extract_ctx_from_text(text: str):
    m = _CTX_RE.search(text or "")
    if not m:
        return None
    uid, cid, pid, amid = m.groups()
    return int(uid), int(cid), int(pid), (int(amid) if amid else None)

def _try_extract_from_replied_chain(msg: Message):
    """Пробуем достать метки из сообщения-источника или из его родителя (глубина 2)."""
    if not msg.reply_to_message:
        return None
    src = msg.reply_to_message
    ctx = _extract_ctx_from_text((src.text or src.caption or ""))
    if ctx:
        return ctx
    if src.reply_to_message:
        return _extract_ctx_from_text((src.reply_to_message.text or src.reply_to_message.caption or ""))
    return None

# ---------- Форматирование служебных сообщений (HTML) ----------
def _hdr_admin_to_user(link: str | None, uid: int, cid: int, pid: int, amid: int, caption: str | None) -> str:
    base = (
        "✅ <b>Сообщение от администратора</b>\n"
        f"Пост: {html.escape(link) if link else f'chat_id={cid}, msg_id={pid}'}\n\n"
    )
    quote = f"<blockquote>{html.escape(caption)}</blockquote>\n\n" if caption else ""
    markers = f"<tg-spoiler>UID:{uid} CID:{cid} PID:{pid} AMID:{amid}</tg-spoiler>"
    hint = "\n\nОтветьте на это сообщение, чтобы написать Администратору."
    return base + quote + markers + hint

def _hdr_user_to_admin_new(who: str, link: str | None, uid: int, cid: int, pid: int, caption: str | None) -> str:
    base = (
        "💬 <b>Новый комментарий</b>\n"
        f"Пользователь: {html.escape(who)}\n"
        f"Пост: {html.escape(link) if link else f'chat_id={cid}, msg_id={pid}'}\n\n"
    )
    quote = f"<blockquote>{html.escape(caption)}</blockquote>\n\n" if caption else ""
    markers = f"<tg-spoiler>UID:{uid} CID:{cid} PID:{pid}</tg-spoiler>"
    hint = "\nОтветьте на это сообщение, чтобы написать пользователю."
    return base + quote + markers + hint

def _hdr_user_to_admin_reply(who: str, link: str | None, uid: int, cid: int, pid: int, caption: str | None) -> str:
    base = (
        "↩️ <b>Ответ от пользователя</b>\n"
        f"Пользователь: {html.escape(who)}\n"
        f"Пост: {html.escape(link) if link else f'chat_id={cid}, msg_id={pid}'}\n\n"
    )
    quote = f"<blockquote>{html.escape(caption)}</blockquote>\n\n" if caption else ""
    markers = f"<tg-spoiler>UID:{uid} CID:{cid} PID:{pid}</tg-spoiler>"
    return base + quote + markers
# Имя канала
async def _channel_display_name(bot, cid: int) -> str:
    # сначала пробуем из БД
    async with SessionLocal() as session:
        ch = (await session.execute(select(Channel).where(Channel.chat_id == cid))).scalar_one_or_none()
    if ch:
        if ch.title:
            return ch.title
        if ch.username:
            return f"@{ch.username}"
        return str(cid)
    # если в БД нет — пробуем спросить у Telegram
    try:
        chat = await bot.get_chat(cid)
        return chat.title or (f"@{chat.username}" if chat.username else str(cid))
    except Exception:
        return str(cid)

# ---------- Безопасная пересылка (голос/кружок -> документ при запрете) ----------
async def _safe_copy_or_send(bot, target_chat_id: int, src_msg: Message, reply_to_message_id: int | None = None):
    """
    copy_message; если VOICE/VIDEO_NOTE запрещены — шлём как document с подписью.
    """
    try:
        return await bot.copy_message(
            chat_id=target_chat_id,
            from_chat_id=src_msg.chat.id,
            message_id=src_msg.message_id,
            reply_to_message_id=reply_to_message_id
        )
    except TelegramBadRequest as e:
        text = str(e)
        cap = (src_msg.caption or "").strip()
        if "VOICE_MESSAGES_FORBIDDEN" in text and src_msg.voice:
            return await bot.send_document(
                chat_id=target_chat_id,
                document=src_msg.voice.file_id,
                caption=(html.escape(cap) if cap else None),
                parse_mode="HTML",
                reply_to_message_id=reply_to_message_id
            )
        if "VIDEO_MESSAGES_FORBIDDEN" in text and src_msg.video_note:
            return await bot.send_document(
                chat_id=target_chat_id,
                document=src_msg.video_note.file_id,
                caption=(html.escape(cap) if cap else None),
                parse_mode="HTML",
                reply_to_message_id=reply_to_message_id
            )
        raise

# ---------- Подготовка media для sendMediaGroup ----------
def _as_input_media(m: Message, with_caption: bool, override_caption: str | None = None):
    """
    Если override_caption задан — кладём его (HTML) в первый элемент группы.
    """
    cap = override_caption if (with_caption and override_caption is not None) else ((m.caption or "").strip() if with_caption else None)
    if m.photo:
        return InputMediaPhoto(media=m.photo[-1].file_id,
                               caption=cap if cap else None,
                               parse_mode="HTML")
    if m.video:
        return InputMediaVideo(media=m.video.file_id,
                               caption=cap if cap else None,
                               parse_mode="HTML")
    if m.document:
        return InputMediaDocument(media=m.document.file_id,
                                  caption=cap if cap else None,
                                  parse_mode="HTML")
    return None  # voice / video_note / audio — не поддерживаются в sendMediaGroup

# ---------- Извлечение file_id/типа для записи в БД ----------
def _media_records_from_message(m: Message, mgid: str | None):
    recs = []
    if m.photo:
        p = m.photo[-1]
        recs.append(("photo", p.file_id, p.file_unique_id, mgid))
    elif m.video:
        recs.append(("video", m.video.file_id, m.video.file_unique_id, mgid))
    elif m.document:
        recs.append(("document", m.document.file_id, m.document.file_unique_id, mgid))
    elif m.voice:
        recs.append(("voice", m.voice.file_id, m.voice.file_unique_id, mgid))
    elif m.video_note:
        recs.append(("video_note", m.video_note.file_id, m.video_note.file_unique_id, mgid))
    elif m.audio:
        recs.append(("audio", m.audio.file_id, m.audio.file_unique_id, mgid))
    return recs

# ---------- USER -> ADMIN альбомы ----------
_u2a_buf: dict[str, list[Message]] = defaultdict(list)
_u2a_ctx: dict[str, dict] = {}      # {mgid: {mode:'new'|'reply', who, uid, cid, pid, amid, link, mgid}}
_u2a_task: dict[str, asyncio.Task] = {}
_u2a_album_comment_id: dict[str, int] = {}

async def _flush_u2a(mgid: str):
    parts = _u2a_buf.pop(mgid, [])
    ctx = _u2a_ctx.pop(mgid, None)
    _u2a_task.pop(mgid, None)
    if not parts or not ctx:
        return

    # Заголовок в подпись первого элемента
    cap_text = (parts[0].caption or "").strip() if parts else None
    if ctx["mode"] == "reply":
        header = _hdr_user_to_admin_reply(ctx["who"], ctx["link"], ctx["uid"], ctx["cid"], ctx["pid"], cap_text or None)
        reply_to = ctx["amid"] or None
    else:
        header = _hdr_user_to_admin_new(ctx["who"], ctx["link"], ctx["uid"], ctx["cid"], ctx["pid"], cap_text or None)
        reply_to = None

    media = []
    for i, p in enumerate(parts):
        im = _as_input_media(p, with_caption=(i == 0), override_caption=header if i == 0 else None)
        if im:
            media.append(im)

    if media:
        try:
            await parts[0].bot.send_media_group(
                chat_id=config.admin_chat_id,
                media=media,
                reply_to_message_id=reply_to
            )
        except Exception:
            # fallback: по одному (потом якорь отдельным постом)
            for p in parts:
                try:
                    await p.bot.copy_message(config.admin_chat_id, p.chat.id, p.message_id, reply_to_message_id=reply_to)
                except Exception:
                    pass
            # отдельный якорь, если альбом не отправился подписью
            await parts[0].bot.send_message(config.admin_chat_id, header, reply_to_message_id=reply_to)

    # сброс связки для новых альбомов
    if ctx.get("mode") == "new":
        _u2a_album_comment_id.pop(ctx.get("mgid"), None)

async def _delayed_flush_u2a(mgid: str):
    await asyncio.sleep(0.7)
    await _flush_u2a(mgid)

# ---------- ADMIN -> USER альбомы ----------
_a2u_buf: dict[str, list[Message]] = defaultdict(list)
_a2u_ctx: dict[str, dict] = {}      # {mgid: {uid, cid, pid, amid, link}}
_a2u_task: dict[str, asyncio.Task] = {}

async def _flush_a2u(mgid: str):
    parts = _a2u_buf.pop(mgid, [])
    ctx = _a2u_ctx.pop(mgid, None)
    _a2u_task.pop(mgid, None)
    if not parts or not ctx:
        return

    cap_text = (parts[0].caption or "").strip() if parts else None
    header = _hdr_admin_to_user(ctx["link"], ctx["uid"], ctx["cid"], ctx["pid"], ctx["amid"], cap_text or None)

    media = []
    for i, p in enumerate(parts):
        im = _as_input_media(p, with_caption=(i == 0), override_caption=header if i == 0 else None)
        if im:
            media.append(im)

    if media:
        try:
            await parts[0].bot.send_media_group(
                chat_id=ctx["uid"],
                media=media
            )
        except Exception:
            # fallback: по одному и отдельный текстом якорь
            for p in parts:
                try:
                    await p.bot.copy_message(ctx["uid"], p.chat.id, p.message_id)
                except Exception:
                    pass
            await parts[0].bot.send_message(ctx["uid"], header)

def make_intro_text() -> str:
    channel_link = '<a href="https://t.me/w2wcom">WWW.com</a>'
    return (
        "<b>Анонимные комментарии к постам канала</b>\n\n"

        "🧪 <b>Статус</b>: закрытый бета-тест для канала "
        f"{channel_link}. Доступ отключён.\n\n"

        "<b>Как пользоваться</b>:\n"
        "1) Откройте нужный пост в канале.\n"
        "2) Нажмите «💬 Комментировать» под постом.\n"
        "3) Отправьте одно сообщение с текстом/медиа.\n"
        "4) Когда админ ответит, просто <i>ответьте на его сообщение</i> здесь, чтобы продолжить диалог.\n\n"

        "<b>Что можно отправить</b>:\n"
        "✍️ текст\n"
        "🖼️ фото (в т.ч. альбом)\n"
        "🎬 видео\n"
        "📎 документ\n"
        "🎧 аудио\n"
        "🎙️ голосовое\n"
        "🎥 кружок\n\n"

        "<b>Команды</b>:\n"
        "/cancel - отменить текущий комментарий\n\n"

        "<b>Для владельцев канала</b>:\n"
        "Одна кнопка под постом, <i>настраиваемая</i> подпись и эмодзи.\n"
        "Поддержка фото-альбомов и медиа: альбомы собираются корректно, а к медиа добавляется «якорь» для ответа.\n"
        "Встроенный антиспам - защита от флудеров.\n"
        "Умная маршрутизация ответов (метки UID/CID/PID/AMID) - отвечаете на служебное сообщение, и оно уходит нужному пользователю.\n\n"

        f"{BOT_SIGNATURE}"
    )

async def _delayed_flush_a2u(mgid: str):
    await asyncio.sleep(0.7)
    await _flush_a2u(mgid)

# ===================== /start (кнопка) =====================
@router.message(F.chat.type == ChatType.PRIVATE, F.text.startswith("/start"))
async def start_any(m: Message):
    parts = m.text.strip().split(maxsplit=1)
    payload = parts[1] if len(parts) > 1 else ""

    # Без payload: общий экран «как пользоваться»
    if not payload:
        allowed = list(config.allowed_channels or [])
        if len(allowed) == 1:
            name = await _channel_display_name(m.bot, allowed[0])
            text = (
                f"💬 Анонимный комментарий для админа канала <b>{html.escape(name)}</b>\n\n"
                f"<b>Что можно отправить</b>:\n"
                f"✍️ текст\n"
                f"🖼️ фото (в т.ч. альбом)\n"
                f"🎬 видео\n"
                f"📎 документ\n"
                f"🎧 аудио\n"
                f"🎙️ голосовое\n"
                f"🎥 кружок\n\n"
                f"<b>Как это работает</b>:\n"
                f"1) Открой пост в канале {html.escape(name)}.\n"
                f"2) Нажми кнопку «💬 Комментировать» под постом.\n"
                f"3) Бот откроется с привязкой к посту.\n\n"
                f"Отмена - /cancel\n\n"
                f"{BOT_SIGNATURE}"
            )
        else:
            text = make_intro_text()
        return await m.answer(text)

    # С payload (кнопка под постом): конкретный канал
    try:
        chat_id_s, post_id_s = payload.split("msg", 1)
        channel_chat_id = int(chat_id_s)
        post_id = int(post_id_s)
    except Exception:
        return await m.answer("Некорректная ссылка. Нажмите кнопку под постом ещё раз.")

    _pending[m.from_user.id] = (channel_chat_id, post_id)

    # регистрация/обновление пользователя
    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.tg_id == m.from_user.id))).scalar_one_or_none()
        if not user:
            user = User(tg_id=m.from_user.id, username=m.from_user.username)
            session.add(user)
        else:
            user.username = m.from_user.username
        await session.commit()

    channel_name = await _channel_display_name(m.bot, channel_chat_id)

    text = (
        f"📝 Комментарий для админа канала <b>{html.escape(channel_name)}</b>\n\n"
        f"Отправь <u>одним сообщением</u> - я передам его администратору.\n\n"
        f"<b>Можно отправить</b>:\n"
        f"✍️ текст\n"
        f"🖼️ фото (в т.ч. альбом)\n"
        f"🎬 видео\n"
        f"📎 документ\n"
        f"🎧 аудио\n"
        f"🎙️ голосовое\n"
        f"🎥 кружок\n\n"
        f"Отмена - /cancel\n\n"
        f"{BOT_SIGNATURE}"
    )
    await m.answer(text)

# ===================== /cancel =====================
@router.message(F.chat.type == ChatType.PRIVATE, F.text == "/cancel")
async def cancel(m: Message):
    _pending.pop(m.from_user.id, None)
    await m.answer("Отменено. Нажмите кнопку под постом ещё раз.")

# ===================== АДМИН -> ПОЛЬЗОВАТЕЛЬ (текст) =====================
@router.message(F.chat.id == config.admin_chat_id, F.reply_to_message, (F.text | F.caption))
async def admin_reply_text(m: Message):
    ctx = _try_extract_from_replied_chain(m)
    if not ctx:
        return await m.reply("Не вижу меток адресата. Ответьте именно на уведомление бота.")
    uid, cid, pid, _ = ctx

    chat = await m.bot.get_chat(cid)
    link = build_post_link(cid, chat.username, pid)
    body = (m.text or m.caption or "").strip()

    text = _hdr_admin_to_user(link, uid, cid, pid, m.message_id, caption=body or None)
    await m.bot.send_message(uid, text)

# ===================== АДМИН -> ПОЛЬЗОВАТЕЛЬ (медиа/альбом) =====================
@router.message(F.chat.id == config.admin_chat_id, F.reply_to_message, (F.photo | F.video | F.document | F.voice | F.audio | F.video_note))
async def admin_reply_media(m: Message):
    ctx = _try_extract_from_replied_chain(m)
    if not ctx:
        return await m.reply("Не вижу меток адресата. Ответьте именно на уведомление бота.")
    uid, cid, pid, _ = ctx

    chat = await m.bot.get_chat(cid)
    link = build_post_link(cid, chat.username, pid)
    cap = (m.caption or "").strip()
    header = _hdr_admin_to_user(link, uid, cid, pid, m.message_id, caption=cap or None)

    # альбом (photo/video/document)
    if m.media_group_id and (m.photo or m.video or m.document):
        mgid = m.media_group_id
        _a2u_buf[mgid].append(m)
        if mgid not in _a2u_ctx:
            _a2u_ctx[mgid] = {"uid": uid, "cid": cid, "pid": pid, "amid": m.message_id, "link": link}
        if mgid not in _a2u_task:
            _a2u_task[mgid] = asyncio.create_task(_delayed_flush_a2u(mgid))
        return

    # одиночные
    if m.photo:
        await m.bot.send_photo(uid, m.photo[-1].file_id, caption=header, parse_mode="HTML")
    elif m.video:
        await m.bot.send_video(uid, m.video.file_id, caption=header, parse_mode="HTML")
    elif m.document:
        await m.bot.send_document(uid, m.document.file_id, caption=header, parse_mode="HTML")
    elif m.audio:
        await m.bot.send_audio(uid, m.audio.file_id, caption=header, parse_mode="HTML")
    else:
        # voice / video_note → безопасная пересылка и отдельный якорь
        await _safe_copy_or_send(m.bot, uid, m)
        await asyncio.sleep(0.3)
        await m.bot.send_message(uid, header)

# ===================== ПОЛЬЗОВАТЕЛЬ -> АДМИН (текст) =====================
@router.message(F.chat.type == ChatType.PRIVATE, (F.text | F.caption))
async def user_text(m: Message):
    text = (m.text or m.caption or "").strip()
    if not text:
        return await m.answer("Пустой комментарий. Напишите текст.")

    # переписка (reply на бота)
    ctx = _try_extract_from_replied_chain(m) if m.reply_to_message else None
    if ctx:
        uid, cid, pid, amid = ctx
        chat = await m.bot.get_chat(cid)
        link = build_post_link(cid, chat.username, pid)
        who = f"@{m.from_user.username}" if m.from_user.username else f"id:{m.from_user.id}"

        msg_html = _hdr_user_to_admin_reply(who, link, m.from_user.id, cid, pid, caption=text or None)
        await m.bot.send_message(config.admin_chat_id, msg_html, reply_to_message_id=amid or None)
        return await m.answer("✅ Отправлено администратору.")

    # новый комментарий
    ctx2 = _pending.get(m.from_user.id)
    if not ctx2:
        return await m.answer("Чтобы оставить комментарий, нажмите кнопку под постом.")
    cid, pid = ctx2

    async with SessionLocal() as session:
        ok, _ = await check_and_hit(session, m.from_user.id)
        if not ok:
            return await m.answer("Слишком часто. Попробуйте позже.")
        user = (await session.execute(select(User).where(User.tg_id == m.from_user.id))).scalar_one()
        comment = Comment(channel_chat_id=cid, post_id=pid, user_id=user.id, text=text)
        session.add(comment); await session.commit()

    chat = await m.bot.get_chat(cid)
    link = build_post_link(cid, chat.username, pid)
    who = f"@{m.from_user.username}" if m.from_user.username else f"id:{m.from_user.id}"

    notify = _hdr_user_to_admin_new(who, link, m.from_user.id, cid, pid, caption=text or None)
    await m.bot.send_message(config.admin_chat_id, notify)

    _pending.pop(m.from_user.id, None)
    await m.answer("✅ Готово! Комментарий отправлен.\nОтветьте на это сообщение, чтобы написать Администратору.")
        # после session.commit() нового Comment
    await refresh_comment_button(m.bot, cid, pid)

# ===================== ПОЛЬЗОВАТЕЛЬ -> АДМИН (медиа/альбом) =====================
@router.message(F.chat.type == ChatType.PRIVATE, (F.photo | F.video | F.document | F.voice | F.audio | F.video_note))
async def user_media(m: Message):
    # переписка (reply на бота)
    ctx = _try_extract_from_replied_chain(m) if m.reply_to_message else None
    if ctx:
        uid, cid, pid, amid = ctx
        chat = await m.bot.get_chat(cid)
        link = build_post_link(cid, chat.username, pid)
        who = f"@{m.from_user.username}" if m.from_user.username else f"id:{m.from_user.id}"
        caption = (m.caption or "").strip()
        header = _hdr_user_to_admin_reply(who, link, m.from_user.id, cid, pid, caption=caption or None)

        # альбом?
        if m.media_group_id and (m.photo or m.video or m.document):
            mgid = m.media_group_id
            _u2a_buf[mgid].append(m)
            if mgid not in _u2a_ctx:
                _u2a_ctx[mgid] = {
                    "mode": "reply", "who": who, "uid": m.from_user.id,
                    "cid": cid, "pid": pid, "amid": amid, "link": link, "mgid": mgid
                }
            if mgid not in _u2a_task:
                _u2a_task[mgid] = asyncio.create_task(_delayed_flush_u2a(mgid))
            return

        # одиночные: фото/видео/док/аудио — с подписью; голос/кружок — копия + якорь
        if m.photo:
            await m.bot.send_photo(config.admin_chat_id, m.photo[-1].file_id, caption=header, parse_mode="HTML", reply_to_message_id=amid or None)
        elif m.video:
            await m.bot.send_video(config.admin_chat_id, m.video.file_id, caption=header, parse_mode="HTML", reply_to_message_id=amid or None)
        elif m.document:
            await m.bot.send_document(config.admin_chat_id, m.document.file_id, caption=header, parse_mode="HTML", reply_to_message_id=amid or None)
        elif m.audio:
            await m.bot.send_audio(config.admin_chat_id, m.audio.file_id, caption=header, parse_mode="HTML", reply_to_message_id=amid or None)
        else:
            await _safe_copy_or_send(m.bot, config.admin_chat_id, m, reply_to_message_id=amid or None)
            await asyncio.sleep(0.3)
            await m.bot.send_message(config.admin_chat_id, header, reply_to_message_id=amid or None)

        return await m.answer("✅ Отправлено администратору.")

    # новый комментарий по /start
    ctx2 = _pending.get(m.from_user.id)
    if not ctx2:
        return await m.answer("Чтобы оставить комментарий, нажмите кнопку под постом.")
    cid, pid = ctx2

    caption = (m.caption or "").strip()
    chat = await m.bot.get_chat(cid)
    link = build_post_link(cid, chat.username, pid)
    who = f"@{m.from_user.username}" if m.from_user.username else f"id:{m.from_user.id}"
    header = _hdr_user_to_admin_new(who, link, m.from_user.id, cid, pid, caption=caption or None)

    # альбом?
    if m.media_group_id and (m.photo or m.video or m.document):
        mgid = m.media_group_id

        # БД: Comment + CommentMedia (накапливаем все элементы альбома)
        async with SessionLocal() as session:
            ok, _ = await check_and_hit(session, m.from_user.id)
            if not ok:
                return await m.answer("Слишком часто. Попробуйте позже.")

            user = (await session.execute(select(User).where(User.tg_id == m.from_user.id))).scalar_one()

            if mgid not in _u2a_album_comment_id:
                comment = Comment(channel_chat_id=cid, post_id=pid, user_id=user.id, text=caption or "")
                session.add(comment)
                await session.flush()
                _u2a_album_comment_id[mgid] = comment.id

            comment_id = _u2a_album_comment_id[mgid]
            for t, fid, fuid, g in _media_records_from_message(m, mgid):
                session.add(CommentMedia(
                    comment_id=comment_id,
                    media_type=t,
                    file_id=fid,
                    file_unique_id=fuid,
                    media_group_id=g
                ))
            await session.commit()
            # после session.commit() нового Comment
            await refresh_comment_button(m.bot, cid, pid)

        # Буфер альбома для админа
        _u2a_buf[mgid].append(m)
        if mgid not in _u2a_ctx:
            _u2a_ctx[mgid] = {
                "mode": "new", "who": who, "uid": m.from_user.id,
                "cid": cid, "pid": pid, "amid": None, "link": link, "mgid": mgid
            }
        if mgid not in _u2a_task:
            _u2a_task[mgid] = asyncio.create_task(_delayed_flush_u2a(mgid))

    else:
        # одиночное медиа: БД
        async with SessionLocal() as session:
            ok, _ = await check_and_hit(session, m.from_user.id)
            if not ok:
                return await m.answer("Слишком часто. Попробуйте позже.")
            user = (await session.execute(select(User).where(User.tg_id == m.from_user.id))).scalar_one()
            comment = Comment(channel_chat_id=cid, post_id=pid, user_id=user.id, text=caption or "")
            session.add(comment); await session.flush()
            for t, fid, fuid, g in _media_records_from_message(m, None):
                session.add(CommentMedia(
                    comment_id=comment.id,
                    media_type=t,
                    file_id=fid,
                    file_unique_id=fuid,
                    media_group_id=None
                ))
            await session.commit()
            # после session.commit() нового Comment
            await refresh_comment_button(m.bot, cid, pid)

        # Отправка админу: фото/видео/док/аудио — с подписью; voice/кружок — копия + якорь
        if m.photo:
            await m.bot.send_photo(config.admin_chat_id, m.photo[-1].file_id, caption=header, parse_mode="HTML")
        elif m.video:
            await m.bot.send_video(config.admin_chat_id, m.video.file_id, caption=header, parse_mode="HTML")
        elif m.document:
            await m.bot.send_document(config.admin_chat_id, m.document.file_id, caption=header, parse_mode="HTML")
        elif m.audio:
            await m.bot.send_audio(config.admin_chat_id, m.audio.file_id, caption=header, parse_mode="HTML")
        else:
            await _safe_copy_or_send(m.bot, config.admin_chat_id, m)
            await asyncio.sleep(0.3)
            await m.bot.send_message(config.admin_chat_id, header)

    _pending.pop(m.from_user.id, None)
    await m.answer("✅ Готово! Комментарий отправлен.\nОтветьте на это сообщение, чтобы написать Администратору.")

__all__ = ["router"]