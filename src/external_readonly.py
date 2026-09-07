"""产品外部只读边界：内部结果落盘与获准模型推理不属于外部业务写。"""

from typing import NoReturn


class ExternalWriteForbidden(PermissionError):
    """外部业务写永久不在当前产品范围内。"""


def reject_external_write() -> NoReturn:
    raise ExternalWriteForbidden("平台遵守外部只读边界，不支持外部业务写入、消息发送或结果投递。")
