from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .models import RateLimit
from .config import config

async def check_and_hit(session: AsyncSession, user_tg_id: int) -> tuple[bool, int]:
    now = datetime.utcnow()
    rl = (await session.execute(select(RateLimit).where(RateLimit.user_tg_id == user_tg_id))).scalar_one_or_none()
    if not rl:
        rl = RateLimit(user_tg_id=user_tg_id)
        session.add(rl)
    ok, left = rl.hit(now, config.rate_window_sec, config.rate_per_hour)
    await session.commit()
    return ok, left