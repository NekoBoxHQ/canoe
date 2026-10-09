"""初始化数据库、管理员、示例节点。

用法：
    python seed.py             # 建表 + 管理员 + 示例节点 + demo 用户
    python seed.py --no-node   # 不建示例节点
    python seed.py --schema    # 只输出建表 SQL（不建库）
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta

# Windows 控制台默认 GBK，中文会炸。强制 UTF-8 输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select

from canoe_core import BRAND_CN, SLOGAN_CN
from canoe_server.config import settings
from canoe_server.database import SessionLocal, engine, init_db
from canoe_server.models import Node, User, utcnow
from canoe_server.security import gen_entry_uuid, hash_password


def print_schema() -> None:
    """输出建表 SQL（与 ORM 模型逐字对应，不会漂移）。"""
    from sqlalchemy.schema import CreateIndex, CreateTable

    from canoe_server import models  # noqa: F401

    lines = []
    for table in models.Base.metadata.sorted_tables:
        lines.append(str(CreateTable(table).compile(dialect=engine.dialect)).strip() + ";")
        for index in table.indexes:
            lines.append(str(CreateIndex(index).compile(dialect=engine.dialect)).strip() + ";")
        lines.append("")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{BRAND_CN} · {SLOGAN_CN}")
    parser.add_argument("--no-node", action="store_true", help="不创建示例节点")
    parser.add_argument("--schema", action="store_true", help="只打印建表 SQL")
    args = parser.parse_args()

    if args.schema:
        print_schema()
        return

    init_db()
    db = SessionLocal()
    try:
        # --- 管理员 ---
        admin = db.scalars(select(User).where(User.username == settings.admin_username)).first()
        if admin is None:
            admin = User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                role="admin",
                status="active",
                max_devices=99,
                remark="内置管理员",
            )
            db.add(admin)
            db.commit()
            print(f"[+] 管理员: {settings.admin_username} / {settings.admin_password}")
            print("    ⚠ 请立刻改掉（.env 的 ADMIN_PASSWORD，或后台改）")
        else:
            print(f"[=] 管理员已存在: {settings.admin_username}")

        # --- 示例节点 ---
        if not args.no_node:
            name = "示例节点-轻舟"
            if db.scalars(select(Node).where(Node.name == name)).first() is None:
                node = Node(
                    name=name,
                    remark="seed 生成的占位节点，请到后台改成你的真实节点",
                    enabled=False,  # 默认停用，避免误当成可用节点
                    sort_order=10,
                    entry_host="canoe.example.com",
                    entry_port=443,
                    entry_uuid=gen_entry_uuid(),
                    entry_path="/e/hk01",
                    entry_sni="canoe.example.com",
                    entry_transport="ws",
                    entry_tls=True,
                    real_protocol="vless",
                    real_host="203.0.113.7",  # TEST-NET-3 占位地址
                    real_port=8443,
                    real_uuid=gen_entry_uuid(),
                    real_flow="xtls-rprx-vision",
                    real_tls=True,
                    real_sni="real.example.com",
                    real_fingerprint="chrome",
                    real_network="tcp",
                )
                db.add(node)
                db.commit()
                print(f"[+] 示例节点: {name}（默认停用）")
            else:
                print(f"[=] 示例节点已存在: {name}")

        # --- demo 用户，方便立刻联调 ---
        if db.scalars(select(User).where(User.username == "demo")).first() is None:
            demo = User(
                username="demo",
                password_hash=hash_password("canoe-demo-123"),
                role="user",
                status="active",
                expire_at=utcnow() + timedelta(days=7),
            )
            db.add(demo)
            db.commit()
            print("[+] 测试用户: demo / canoe-demo-123（7 天）")

        print("\n完成。启动服务端: python run.py")
        print("接口文档: http://127.0.0.1:8000/docs")
    finally:
        db.close()


if __name__ == "__main__":
    main()
