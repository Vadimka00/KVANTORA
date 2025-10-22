from datetime import datetime, timedelta
from sqlalchemy import BigInteger, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    comments: Mapped[list["Comment"]] = relationship(back_populates="user")

class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    post_id: Mapped[int] = mapped_column(Integer, index=True)  # message_id в канале
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="comments")

class RateLimit(Base):
    __tablename__ = "rate_limits"
    user_tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hour_bucket_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hour_count: Mapped[int] = mapped_column(Integer, default=0)  # ниже – защита в коде

    def hit(self, now: datetime, window_sec: int, per_hour: int) -> tuple[bool, int]:
        # --- страховка от NULL в БД
        if self.hour_count is None:
            self.hour_count = 0

        # (1) мелкое окно по секундам
        if self.last_ts and (now - self.last_ts).total_seconds() < window_sec:
            return False, max(0, per_hour - (self.hour_count or 0))

        # (2) почасовой бакет
        hb = self.hour_bucket_start or now
        if (now - hb) >= timedelta(hours=1):
            self.hour_bucket_start = now
            self.hour_count = 0

        if self.hour_count >= per_hour:
            return False, 0

        self.last_ts = now
        self.hour_count += 1
        return True, per_hour - self.hour_count
    
class CommentMedia(Base):
    __tablename__ = "comment_medias"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"), index=True)
    media_type: Mapped[str] = mapped_column(String(16))        # photo|video|document|voice|video_note|audio
    file_id: Mapped[str] = mapped_column(String(512))
    file_unique_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    media_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # чтобы понимать альбом
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)