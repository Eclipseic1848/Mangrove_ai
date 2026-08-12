"""
Agent 配置模块

包含 Agent 相关的配置类。
"""
from .text_processing_config import TextProcessingConfig, default_text_config

__all__ = [
    "TextProcessingConfig",
    "default_text_config",
]
