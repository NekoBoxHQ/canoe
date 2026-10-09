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


#: 后加的列 -> 补列语句。create_all 只建**缺的表**，已有表少字段它不管，
#: 所以升级时得自己补，否则老库跑新代码会报 "no such column"。
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "users": {"subscription": "TEXT DEFAULT ''"},
    "tokens": {"sub_key": "VARCHAR(64) DEFAULT ''"},
    "nodes": {"link": "TEXT DEFAULT ''"},
}


def _ensure_columns() -> None:
    """把老库缺的列补上。够用就好 —— 真要改类型/加约束请上 Alembic。"""
    from sqlalchemy import inspect, text as sql_text

    existing = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in existing:
                continue
            have = {c["name"] for c in inspect(engine).get_columns(table)}
            for name, ddl in columns.items():
                if name not in have:
                    conn.execute(sql_text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


#: 老库里"列约束变了"的表 —— SQLite 改不了列的 NOT NULL，只能整张重建。
#: 只放**易失数据**：会话就是"谁现在在线"，升级时丢掉毫无损失，
#: 用户重新启航就会重新建一条。别往这里加 users/nodes 这种真数据。
_REBUILD_IF_STALE: list[tuple[str, str, bool]] = [
    # (表, 列, 该列是否应当可空)
    ("sessions", "node_id", True),   # 订阅模式不再分配节点，改成可空
]


#: 老库里"整张表都不作数了"的：这些列属于已经被删掉的模型（中转层的
#: 入口/真实节点），新代码根本不认识它们。老行留着也没用 —— 那是
#: 渲染 sing-box 配置用的参数，不是链接。但 SQLite 删不掉列，
#: 而它们还挂着 NOT NULL，新代码 INSERT 时必炸（踩过：建节点 500）。
_OBSOLETE_COLUMNS: dict[str, tuple[str, ...]] = {
    # 中转层的入口/真实节点参数 —— 新代码根本不认识这些列
    "nodes": ("entry_host", "entry_port", "entry_uuid", "real_host", "real_port"),
    # 入口凭证 —— 订阅模式不发 ticket 了。sessions 是易失数据，重建无妨
    "sessions": ("ticket_hash",),
}


def _drop_stale_tables() -> None:
    from sqlalchemy import inspect, text as sql_text

    insp = inspect(engine)
    names = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, want_nullable in _REBUILD_IF_STALE:
            if table not in names:
                continue
            for col in insp.get_columns(table):
                if col["name"] == column and bool(col["nullable"]) != want_nullable:
                    conn.execute(sql_text(f"DROP TABLE {table}"))
                    break
        for table, gone in _OBSOLETE_COLUMNS.items():
            if table not in names:
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            if have & set(gone):
                conn.execute(sql_text(f"DROP TABLE {table}"))


def init_db() -> None:
    """建表 + 补列。生产建议改用 Alembic 迁移（见 docs/03-database.md）。"""
    from . import models  # noqa: F401  确保模型注册到 metadata

    _drop_stale_tables()
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
