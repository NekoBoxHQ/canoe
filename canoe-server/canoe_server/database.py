"""数据库引擎与会话。"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

connect_args = {"check_same_thread": False} if settings.is_sqlite else {}

engine = create_engine(
    settings.database_url, connect_args=connect_args, pool_pre_ping=True, future=True
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表。生产建议改用 Alembic 迁移（见 docs/03-database.md）。"""
    from . import models  # noqa: F401  确保模型注册到 metadata

    Base.metadata.create_all(bind=engine)
