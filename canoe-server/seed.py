"""初始化数据库、管理员、测试用户。

节点不由这里造 —— 管理员在面板上贴链接自己加。

用法：
    python seed.py             # 建表 + 管理员 + demo 用户
    python seed.py --schema    # 只输出建表 SQL（不建库）
"""
from __future__ import annotations

import argparse
import secrets
import sys
from datetime import timedelta

# Windows 控制台默认 GBK，中文会炸。强制 UTF-8 输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select

from canoe_core import BRAND_CN, SLOGAN_CN
from canoe_server.config import DEFAULT_ADMIN_PASSWORD, settings
from canoe_server.database import SessionLocal, engine, init_db
from canoe_server.models import Node, User, utcnow
from canoe_server.security import hash_password


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
            # 还是内置默认值 -> 换一把随机的。
            # 仓库是公开的，canoe-admin-123 这个字符串谁都能从 GitHub 上搜到；
            # 照着文档手动部署的人要是真用了它，等于在公网上开了一个
            # 密码人尽皆知的管理面板。宁可随机生成再打印一次。
            password = settings.admin_password
            generated = password == DEFAULT_ADMIN_PASSWORD
            if generated:
                password = secrets.token_urlsafe(18)

            admin = User(
                username=settings.admin_username,
                password_hash=hash_password(password),
                role="admin",
                status="active",
                max_devices=99,
                remark="内置管理员",
            )
            db.add(admin)
            db.commit()
            if generated:
                print(f"[+] 管理员: {settings.admin_username}")
                print(f"    密码  : {password}")
                print("    （没配 ADMIN_PASSWORD，所以现生成了一把随机的。")
                print("      存好它，或者去 .env 里设一个再重跑。后台也能改。）")
            else:
                print(f"[+] 管理员: {settings.admin_username} / {password}")
                print("    ⚠ 请立刻改掉（.env 的 ADMIN_PASSWORD，或后台改）")
        else:
            print(f"[=] 管理员已存在: {settings.admin_username}")

        # --- 不造占位节点 ---
        # 以前这里会塞一个「示例节点-轻舟」占位。现在节点就是管理员自己贴的
        # 一行链接，占位节点只会让人以为"已经配好了"，反而添乱。
        if db.scalars(select(Node)).first() is None:
            print("[i] 还没有节点 —— 去面板「节点」页粘一行链接就有了")
            print("    （支持 ss:// vmess:// vless:// trojan://）")

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

        # 这两行以前写的是"启动服务端: python run.py / 接口文档: http://127.0.0.1:8000/docs"，
        # 在 systemd 安装里是彻头彻尾的误导：服务由 canoe-api 托管，
        # 端口也不是 8000。分开写，各自说各自的。
        print()
        print("数据库已就绪。")
        print("  本地开发： python run.py        （http://127.0.0.1:8000 ）")
        print("  生产环境： 由 systemd 托管，用 sudo canoe 启停")
    finally:
        db.close()


if __name__ == "__main__":
    main()
