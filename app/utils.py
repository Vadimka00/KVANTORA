def build_post_link(chat_id: int, username: str | None, message_id: int) -> str | None:
    if username:
        return f"https://t.me/{username}/{message_id}"
    # приватный: chat_id вида -1001234567890 -> internal 1234567890
    internal = str(abs(chat_id))
    if internal.startswith("100"):
        internal = internal[3:]
    return f"https://t.me/c/{internal}/{message_id}"