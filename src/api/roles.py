"""认证入口和账号写事务共用同一角色顺序。"""

# 超级管理员仅由系统引导设定，管理员只能管理严格低于自己的角色。
ROLE_LEVEL = {"super_admin": 3, "admin": 2, "user": 1}


def role_level(role: str | None) -> int:
    return ROLE_LEVEL.get(role or "user", 1)
