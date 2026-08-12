"""
解析工具函数

提供统一的 LLM 输出解析功能，支持带重试机制的解析（自动请求 LLM 重新生成）。
"""
import logging
from typing import Optional, TypeVar, Callable, Any

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


# ==================== 类型定义 ====================

T = TypeVar('T', bound=BaseModel)


# ==================== 常量定义 ====================

# 错误消息模板
LLM_RETRY_PROMPT_TEMPLATE = (
    "你刚才返回的内容无法通过 Pydantic 校验，错误如下：\n{error}\n"
    "请直接重新生成符合格式要求的 JSON，不要解释。"
)

# 日志消息模板
LOG_SUCCESS_RETRY_TEMPLATE = "✅ 成功解析输出（尝试 {attempt}/{max_attempts}）"
LOG_RETRY_WARNING_TEMPLATE = (
    "⚠️ 解析失败（尝试 {attempt}/{max_attempts}），请求 LLM 重新生成: {error}"
)
LOG_MAX_RETRY_ERROR_TEMPLATE = "❌ 解析失败，已达到最大重试次数: {error}"
LOG_LLM_ERROR_TEMPLATE = "LLM 重新生成失败: {error}"


# ==================== 带重试的解析 ====================

def parse_with_retry(
    text: str,
    output_parser: PydanticOutputParser,
    llm_invoke: Callable[[str], Any],
    max_retry: int = 2,
    verbose: bool = False
) -> Optional[BaseModel]:
    """带重试机制的解析方法
    
    当 Pydantic 解析失败时，会自动请求 LLM 重新生成符合格式要求的内容，
    最多重试 max_retry 次。
    
    流程：
    1. 尝试使用 output_parser 解析文本
    2. 如果解析失败，请求 LLM 重新生成内容
    3. 重复步骤 1-2，直到成功或达到最大重试次数
    
    Args:
        text: 要解析的文本内容（LLM 返回的原始内容）
        output_parser: Pydantic 输出解析器，用于解析和验证格式
        llm_invoke: LLM 调用函数，接受字符串并返回响应对象（需有 content 属性）
        max_retry: 最大重试次数（默认 2 次，即最多尝试 max_retry + 1 次）
        verbose: 是否显示详细信息
        
    Returns:
        解析后的 Pydantic 模型实例
        
    Raises:
        ValidationError: 当所有重试都失败时抛出最后一次的异常
        Exception: 当 LLM 调用失败时可能抛出其他异常
    """
    current_text = text
    max_attempts = max_retry + 1
    
    for attempt in range(max_attempts):
        try:
            parsed_output = output_parser.parse(current_text)
            if verbose:
                logger.info(LOG_SUCCESS_RETRY_TEMPLATE.format(
                    attempt=attempt + 1,
                    max_attempts=max_attempts
                ))
            return parsed_output
            
        except (ValidationError, Exception) as e:
            # 最后一次尝试失败，抛出异常
            if attempt == max_retry:
                if verbose:
                    logger.error(LOG_MAX_RETRY_ERROR_TEMPLATE.format(error=e))
                raise
            
            # 请求 LLM 重新生成符合格式要求的内容
            current_text = _request_llm_regeneration(
                error=e,
                llm_invoke=llm_invoke,
                attempt=attempt + 1,
                max_attempts=max_attempts,
                verbose=verbose
            )
    
    # 理论上不会到达这里
    return None


def _request_llm_regeneration(
    error: Exception,
    llm_invoke: Callable[[str], Any],
    attempt: int,
    max_attempts: int,
    verbose: bool
) -> str:
    """请求 LLM 重新生成内容
    
    当解析失败时，请求 LLM 根据错误信息重新生成符合格式要求的内容。
    
    Args:
        error: 解析错误异常
        llm_invoke: LLM 调用函数
        attempt: 当前尝试次数
        max_attempts: 最大尝试次数
        verbose: 是否显示详细信息
        
    Returns:
        LLM 重新生成的内容字符串
        
    Raises:
        Exception: 当 LLM 调用失败时抛出
    """
    if verbose:
        logger.warning(LOG_RETRY_WARNING_TEMPLATE.format(
            attempt=attempt,
            max_attempts=max_attempts,
            error=error
        ))
    
    try:
        # 构建重试提示词
        retry_prompt = LLM_RETRY_PROMPT_TEMPLATE.format(error=error)
        
        # 调用 LLM 重新生成
        response = llm_invoke(retry_prompt)
        
        # 提取响应内容
        return _extract_llm_response_content(response)
        
    except Exception as llm_error:
        logger.error(LOG_LLM_ERROR_TEMPLATE.format(error=llm_error))
        raise


def _extract_llm_response_content(response: Any) -> str:
    """提取 LLM 响应内容
    
    Args:
        response: LLM 响应对象
        
    Returns:
        响应内容字符串
    """
    if hasattr(response, 'content'):
        return response.content
    else:
        return str(response)