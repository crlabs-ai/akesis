from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.packages.shared.config import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(database_url: str | None = None, is_test: bool = False) -> AsyncEngine:
    """Returns or initializes the async SQLAlchemy engine."""
    global _engine
    if _engine is None or database_url is not None:
        url = database_url or settings.database_url
        pool_kwargs = (
            {"poolclass": NullPool}
            if is_test or settings.environment == "test"
            else {"pool_pre_ping": True}
        )
        _engine = create_async_engine(
            url,
            echo=False,
            **pool_kwargs,
        )
    return _engine


def get_session_factory(
    database_url: str | None = None,
    is_test: bool = False,
) -> async_sessionmaker[AsyncSession]:
    """Returns or initializes the async session maker."""
    global _session_factory
    if _session_factory is None or database_url is not None:
        engine = get_engine(database_url, is_test=is_test)
        _session_factory = async_sessionmaker(
            bind=engine,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async database session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
