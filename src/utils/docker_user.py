"""让绑定目录的非 root 服务账号与容器使用同一文件身份。"""
import os


def docker_user_args() -> tuple[str, ...]:
    # Windows 没有 POSIX UID；root 宿主也不能覆盖镜像原有的降权用户。
    if os.name != "posix" or os.geteuid() == 0:
        return ()
    return ("--user", f"{os.geteuid()}:{os.getegid()}")
