from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
# asyncpg's own connect timeout defaults to 60s. Left alone, a single stuck
# connection attempt (wrong host, wrong port, service not listening yet) burns
# the entire Railway healthcheck window — the app never gets to attempt 2 of
# _wait_for_database's own retry loop before Railway kills the deploy. Failing
# fast here is what lets that loop actually retry within the window.
engine = create_async_engine(
    settings.database_url, echo=False, future=True, connect_args={"timeout": 5}
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
