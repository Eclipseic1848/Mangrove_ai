"""模型凭证在线密文 Adapter。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from cryptography.fernet import Fernet, InvalidToken


_KEYRING_SCHEMA_VERSION = "fernet-keyring-v1"


class VaultDecryptionError(ValueError):
    """当前密钥代际无法解密 Provider Secret。"""


class FernetCredentialVault:
    """只向 ConnectionBroker 暴露加解密，不向产品 Interface 暴露明文。"""

    def __init__(
        self,
        key: bytes,
        *,
        key_path: Path | None = None,
        inactive_keys: tuple[bytes, ...] = (),
    ) -> None:
        self._active_key = key
        self._inactive_keys = inactive_keys
        self._key_path = key_path
        self._reload_fernets()

    def _reload_fernets(self) -> None:
        self._fernet = Fernet(self._active_key)
        self._decryptors = tuple(
            Fernet(key) for key in (self._active_key, *self._inactive_keys)
        )

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
        stripped = key.strip()
        if stripped.startswith(b"{"):
            try:
                payload = json.loads(stripped.decode("utf-8"))
                active_id = str(payload["active_key_id"])
                keys = {
                    str(key_id): str(value).encode("ascii")
                    for key_id, value in payload["keys"].items()
                }
                if payload["schema_version"] != _KEYRING_SCHEMA_VERSION:
                    raise ValueError
                active = keys.pop(active_id)
                return cls(
                    active,
                    key_path=key_path,
                    inactive_keys=tuple(keys.values()),
                )
            except (KeyError, TypeError, ValueError, UnicodeError) as exc:
                raise ValueError("模型连接主密钥 keyring 格式无效") from exc
        return cls(stripped, key_path=key_path)

    def encrypt(self, secret: str) -> str:
        """把 UTF-8 Secret 加密为可落 SQLite TEXT 的 token。"""

        return self._fernet.encrypt(secret.encode("utf-8")).decode("ascii")

    @property
    def has_inactive_keys(self) -> bool:
        """是否仍处于可恢复的双代际轮换窗口。"""

        return bool(self._inactive_keys)

    def contains_inactive_key_material(self, content: bytes) -> bool:
        """检查已限定的备份文件是否仍含旧代际明文材料。"""

        return any(key in content for key in self._inactive_keys)

    def file_contains_inactive_key_material(
        self,
        path: str | Path,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> bool:
        """流式检查文件，并保留跨分块边界的匹配窗口。"""

        if chunk_size <= 0:
            raise ValueError("分块大小必须大于 0")
        if not self._inactive_keys:
            return False
        overlap_size = max(len(key) for key in self._inactive_keys) - 1
        overlap = b""
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(chunk_size), b""):
                content = overlap + chunk
                if self.contains_inactive_key_material(content):
                    return True
                overlap = content[-overlap_size:] if overlap_size else b""
        return False

    def active_key_can_decrypt(self, ciphertext: str) -> bool:
        """判断备份密文是否已属于新代际；不返回明文。"""

        try:
            self._fernet.decrypt(ciphertext.encode("ascii"))
        except InvalidToken:
            return False
        return True

    def decrypt(self, ciphertext: str) -> str:
        """仅供 Broker 单次调用内解密。"""

        encoded = ciphertext.encode("ascii")
        for decryptor in self._decryptors:
            try:
                return decryptor.decrypt(encoded).decode("utf-8")
            except InvalidToken:
                continue
        raise VaultDecryptionError("Provider 凭证密文无法解密")

    @staticmethod
    def _key_id(key: bytes) -> str:
        return hashlib.sha256(key).hexdigest()[:16]

    def _write_keyring(self) -> None:
        if self._key_path is None:
            raise RuntimeError("临时 Vault 不支持生产密钥轮换")
        keys = (self._active_key, *self._inactive_keys)
        payload = {
            "schema_version": _KEYRING_SCHEMA_VERSION,
            "active_key_id": self._key_id(self._active_key),
            "keys": {
                self._key_id(key): key.decode("ascii")
                for key in keys
            },
        }
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self._key_path.parent,
                prefix=f".{self._key_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                try:
                    os.chmod(temporary, 0o600)
                except OSError:
                    # Windows 权限边界由密钥目录 ACL 与宿主机管理员共同承担。
                    pass
                json.dump(payload, handle, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self._key_path)
        finally:
            if "temporary" in locals() and temporary.exists():
                # 原子替换失败时不能遗留同时包含新旧 KEK 的明文临时文件。
                temporary.unlink()

    def begin_rotation(self) -> None:
        """进入双代际恢复窗口；重复调用沿用已生成的新代际。"""

        if self._inactive_keys:
            return
        previous = self._active_key
        self._active_key = Fernet.generate_key()
        self._inactive_keys = (previous,)
        self._reload_fernets()
        self._write_keyring()

    def retire_inactive_keys(self) -> None:
        """在线密文全部重加密后销毁旧代际。"""

        self._inactive_keys = ()
        self._reload_fernets()
        self._write_keyring()


__all__ = ["FernetCredentialVault", "VaultDecryptionError"]
