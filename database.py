from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.pool import NullPool

from models import Base
import os


SQLALCHEMY_DATABASE_URL = os.environ.get("SQLALCHEMY_DATABASE_URL")


class AsyncDatabaseManager:
    def __init__(self, db_url: str = SQLALCHEMY_DATABASE_URL):
        self.db_url = db_url

        self.engine = create_async_engine(
            db_url,
            echo=False,
        )

        self.AsyncSessionLocal = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def create_tables(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_tables(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    def get_session(self) -> AsyncSession:
        return self.AsyncSessionLocal()

async_db_manager = AsyncDatabaseManager()
