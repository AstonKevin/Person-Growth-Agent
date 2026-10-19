# -*- coding: utf-8 -*-
"""一次性管理员赋权工具（设计决策 D4）。

用法：
    python scripts/set_admin.py <用户名>
示例：
    python scripts/set_admin.py admin
"""
import sys
import pathlib
from datetime import datetime

# 保证项目根路径在 sys.path 中
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal
from app.crud.user import get_user_by_name


def set_user_admin(username: str) -> bool:
    db = SessionLocal()
    try:
        user = get_user_by_name(db, username)
        if not user:
            print(f"❌ 错误：未找到用户名为 '{username}' 的用户。请先在前端或接口注册该用户。")
            return False

        old_role = user.role
        old_active = user.is_active

        user.role = "admin"
        user.is_active = True
        user.update_time = datetime.now()
        db.commit()

        print(
            f"✅ 成功将用户 '{username}' (ID: {user.id}) 设置为系统管理员！\n"
            f"   - 角色：{old_role} → admin\n"
            f"   - 状态：{old_active} → True"
        )
        return True
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scripts/set_admin.py <用户名>")
        sys.exit(1)

    target_name = sys.argv[1].strip()
    if not target_name:
        print("错误: 用户名不能为空")
        sys.exit(1)

    ok = set_user_admin(target_name)
    sys.exit(0 if ok else 1)
