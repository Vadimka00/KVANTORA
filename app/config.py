# app/config.py
from dataclasses import dataclass, field
from dotenv import load_dotenv
import os

load_dotenv()


def _split_csv_env(*names: str) -> list[str]:
    """Берём первое непустое значение из списка переменных и режем по запятым."""
    for n in names:
        raw = os.getenv(n, "")
        if raw.strip():
            return [s.strip() for s in raw.split(",") if s.strip()]
    return []


@dataclass
class Config:
    # Бот
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    bot_username: str = field(default_factory=lambda: os.getenv("BOT_USERNAME", ""))

    # Админ и канал(ы)
    admin_chat_id: int = field(default_factory=lambda: int(os.getenv("ADMIN_CHAT_ID", "0")))
    allowed_channels: set[int] = field(default_factory=set)

    # БД
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot.db"))

    # Антиспам
    rate_window_sec: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_WINDOW_SEC", "10")))
    rate_per_hour: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_HOUR", "12")))

    # Реакции
    auto_reactions: list[str] = field(default_factory=list)        # Unicode-эмодзи
    custom_reaction_ids: list[str] = field(default_factory=list)   # кастомные ID
    reaction_max_count: int = field(default_factory=lambda: int(os.getenv("REACTION_MAX_COUNT", "2")))
    reaction_attempts: int = field(default_factory=lambda: int(os.getenv("REACTION_ATTEMPTS", "3")))
    reaction_big_prob: float = field(default_factory=lambda: float(os.getenv("REACTION_BIG_PROB", "0.25")))

    # (не используется больше, можно удалить позже)
    deep_link_secret: str = field(default_factory=lambda: os.getenv("DEEP_LINK_SECRET", ""))

    def __post_init__(self):
        # allowed_channels: "-100123,-100456"
        raw_allowed = os.getenv("ALLOWED_CHANNEL_IDS", "")
        if raw_allowed.strip():
            self.allowed_channels = {int(x) for x in raw_allowed.split(",") if x.strip()}

        # списки реакций
        self.auto_reactions = _split_csv_env("AUTO_REACTIONS", "AUTO_REACTION")
        self.custom_reaction_ids = _split_csv_env("CUSTOM_REACTION_IDS")

        # нормализуем границы
        if self.reaction_max_count < 1:
            self.reaction_max_count = 1
        if self.reaction_attempts < 1:
            self.reaction_attempts = 1
        # clamp 0..1
        self.reaction_big_prob = max(0.0, min(1.0, self.reaction_big_prob))


config = Config()