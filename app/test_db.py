import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    url = "mysql+aiomysql://k_bot:Kv%40nt0r%40%21@155.212.164.57:3306/k-channel-test?charset=utf8mb4"
    engine = create_async_engine(url, pool_pre_ping=True, pool_recycle=3600)
    async with engine.begin() as conn:
        res = await conn.execute(text("SELECT 1"))
        print("✅ Подключение успешно:", res.scalar())
    await engine.dispose()

asyncio.run(main())