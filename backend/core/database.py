"""
设计院 (Studio) - 数据库配置
使用独立的 SQLite 数据库, 与主项目完全隔离
"""
import os
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

DATABASE_DIR = os.environ.get("STUDIO_DATA_PATH", "/data")
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_DIR}/studio.db"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=5,
    pool_timeout=30,
    pool_recycle=3600,
    connect_args={"timeout": 10},  # SQLite busy_timeout 10秒 (写锁已提前释放)
)


# 每个新连接启用 WAL 模式 + 外键约束
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    """数据库会话依赖注入"""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """初始化数据库表"""
    async with engine.begin() as conn:
        from studio.backend.models import Base as ModelBase  # noqa
        await conn.run_sync(ModelBase.metadata.create_all)
