from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from .config import config

class Base(DeclarativeBase):
    pass

engine = create_async_engine(
    config.database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,   # восстанавливает отвалившиеся коннекты
    pool_recycle=3600     # перебирает соединения раз в час (MySQL best‑practice)
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_models():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)