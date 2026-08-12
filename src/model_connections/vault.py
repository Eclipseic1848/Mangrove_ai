"""模型凭证在线密文 Adapter。"""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet


class FernetCredentialVault:
    """只向 ConnectionBroker 暴露加解密，不向产品 Interface 暴露明文。"""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    @classmethod
    def generate(cls) -> "FernetCredentialVault":
        """创建仅供测试或显式注入的临时 Vault。"""

        return cls(Fernet.generate_key())

    @classmethod
    def from_key_file(cls, path: str | Path) -> "FernetCredentialVault":
        """加载独立主密钥；首次使用时以仅当前用户可读权限创建。"""

        key_path = Path(path)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            key = key_path.read_bytes()
        except FileNotFoundError:
            key = Fernet.generate_key()
            try:
                with key_path.open("xb") as handle:
                    handle.write(key)
            except FileExistsError:
                key = key_path.read_bytes()
            else:
                try:
                    os.chmod(key_path, 0o600)
                except OSError:
                    # Windows ACL 不完全等价于 POSIX mode；信任边界仍包含宿主机管理员。
                    pass
        return cls(key.strip())

    def encrypt(self, secret: str) -> str:
        """把 UTF-8 Secret 加密为可落 SQLite TEXT 的 token。"""

        return self._fernet.encrypt(secret.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """仅供 Broker 单次调用内解密。"""

        return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
