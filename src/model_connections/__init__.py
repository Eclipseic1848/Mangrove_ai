"""模型连接深 Module 的公开入口。"""

from .broker import (
    ConnectionBroker,
    ConnectionError,
    ConnectionValidationError,
    GrantError,
    get_default_broker,
)
from .catalog import ProviderPreset, public_presets
from .contracts import AccessGrant, ConnectionBinding, RelayResponse

__all__ = [
    "ConnectionBroker",
    "ConnectionError",
    "ConnectionValidationError",
    "GrantError",
    "AccessGrant",
    "ConnectionBinding",
    "ProviderPreset",
    "RelayResponse",
    "get_default_broker",
    "public_presets",
]
