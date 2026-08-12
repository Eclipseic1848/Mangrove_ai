"""
浏览器 evaluate 结果解析工具

专门用于解析 browser_evaluate 工具返回的结果，提取文字内容并保存到文件。
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union
from langchain_core.tools import tool, BaseTool

logger = logging.getLogger(__name__)

# 自动保存阈值：如果输入超过此长度且未指定输出文件，自动保存到临时文件
AUTO_SAVE_THRESHOLD = 2000  # 字符数


def _parse_evaluate_result(result: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    解析 browser_evaluate 返回的结果
    
    browser_evaluate 可能返回：
    1. JSON 字符串格式的结果（包含 content 数组）
    2. 纯文本内容
    3. 已经是解析后的字典
    
    Args:
        result: browser_evaluate 返回的结果字符串或字典
        
    Returns:
        解析后的字典，包含提取的文字内容
    """
    parsed_data = {
        "raw_result": result,
        "text_content": "",
        "structured_data": None,
    }
    
    # 如果已经是字典，直接使用
    if isinstance(result, dict):
        parsed_data["structured_data"] = result
        # 尝试提取文本内容
        content = result.get("content", [])
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict):
                    # 提取 text 类型的内容
                    if item.get("type") == "text":
                        texts.append(item.get("text", ""))
                    # 或者直接提取 text 字段
                    elif "text" in item:
                        texts.append(item.get("text", ""))
                elif isinstance(item, str):
                    texts.append(item)
            if texts:
                parsed_data["text_content"] = " ".join(texts)
        # 如果 content 是字符串
        elif isinstance(content, str):
            parsed_data["text_content"] = content
        # 如果 result 中有直接的文本字段
        elif "text" in result:
            parsed_data["text_content"] = result.get("text", "")
        elif "result" in result:
            # 尝试从 result 字段提取
            result_value = result.get("result")
            if isinstance(result_value, str):
                parsed_data["text_content"] = result_value
            elif isinstance(result_value, dict):
                parsed_data["structured_data"] = result_value
        return parsed_data
    
    # 如果是字符串，尝试解析为 JSON
    if isinstance(result, str):
        # 处理 MCP 返回的特殊格式：
        # # evaluate_script response
        # Script ran on page and returned:
        # ```json
        # "actual content"
        # ```
        if "# evaluate_script response" in result or "Script ran on page and returned:" in result:
            # 尝试提取代码块中的内容
            # 匹配 ```json 和 ``` 之间的内容
            # 使用贪婪匹配 (.*) 确保匹配完整内容，包括所有换行符
            # 注意：使用 re.DOTALL 使 . 匹配换行符，确保能匹配多行内容
            json_block_match = re.search(r'```json\s*\n(.*?)\n```', result, re.DOTALL)
            if json_block_match:
                json_content = json_block_match.group(1)
                # 不立即 strip，先尝试解析，因为内容可能包含前后空白
                try:
                    json_data = json.loads(json_content)
                    if isinstance(json_data, str):
                        parsed_data["text_content"] = json_data
                    else:
                        parsed_data["structured_data"] = json_data
                        parsed_data["text_content"] = json.dumps(json_data, ensure_ascii=False)
                    logger.debug(f"从 JSON 代码块中提取内容，长度: {len(parsed_data['text_content'])} 字符")
                    return parsed_data
                except (json.JSONDecodeError, TypeError):
                    # 如果解析失败，使用提取的内容作为文本（去除首尾空白）
                    parsed_data["text_content"] = json_content.strip()
                    logger.debug(f"JSON 解析失败，使用原始内容作为文本，长度: {len(parsed_data['text_content'])} 字符")
                    return parsed_data
            
            # 如果没有找到代码块，尝试匹配没有换行的格式：```json\n内容```（内容可能很长，包含换行）
            # 使用更宽松的匹配模式
            json_block_match_loose = re.search(r'```json\s*\n(.*?)```', result, re.DOTALL)
            if json_block_match_loose:
                json_content = json_block_match_loose.group(1).strip()
                try:
                    json_data = json.loads(json_content)
                    if isinstance(json_data, str):
                        parsed_data["text_content"] = json_data
                    else:
                        parsed_data["structured_data"] = json_data
                        parsed_data["text_content"] = json.dumps(json_data, ensure_ascii=False)
                    logger.debug(f"从 JSON 代码块（宽松模式）中提取内容，长度: {len(parsed_data['text_content'])} 字符")
                    return parsed_data
                except (json.JSONDecodeError, TypeError):
                    parsed_data["text_content"] = json_content
                    logger.debug(f"JSON 解析失败（宽松模式），使用原始内容作为文本，长度: {len(json_content)} 字符")
                    return parsed_data
            
            # 如果没有代码块，尝试提取 "Script ran on page and returned:" 后面的内容
            # 使用贪婪匹配确保获取完整内容
            match = re.search(r'Script ran on page and returned:\s*(.*)', result, re.DOTALL)
            if match:
                content = match.group(1).strip()
                # 去除可能的代码块标记
                content = re.sub(r'^```json\s*\n?', '', content, flags=re.MULTILINE)
                content = re.sub(r'\n?```\s*$', '', content)
                content = content.strip()
                
                # 尝试解析为 JSON
                try:
                    json_data = json.loads(content)
                    if isinstance(json_data, str):
                        parsed_data["text_content"] = json_data
                    else:
                        parsed_data["structured_data"] = json_data
                        parsed_data["text_content"] = json.dumps(json_data, ensure_ascii=False)
                    logger.debug(f"从返回内容中提取 JSON，长度: {len(parsed_data['text_content'])} 字符")
                    return parsed_data
                except (json.JSONDecodeError, TypeError):
                    parsed_data["text_content"] = content
                    logger.debug(f"JSON 解析失败，使用原始内容作为文本，长度: {len(content)} 字符")
                    return parsed_data
        
        # 尝试直接解析为 JSON
        try:
            json_data = json.loads(result)
            # 递归调用处理解析后的字典
            return _parse_evaluate_result(json_data)
        except (json.JSONDecodeError, TypeError):
            # 不是 JSON，直接作为文本内容
            parsed_data["text_content"] = result
            return parsed_data
    
    # 其他类型，转换为字符串
    parsed_data["text_content"] = str(result)
    return parsed_data


@tool
def parse_browser_evaluate_result(
    evaluate_result: str,
    output_file: Optional[str] = None,
    extract_text_only: bool = False
) -> str:
    """
    解析 browser_evaluate 工具返回的结果，提取文字内容并可选择保存到文件。
    
    这个工具专门用于处理 browser_evaluate 的返回结果，可以：
    1. 解析 JSON 格式的结果
    2. 提取纯文本内容
    3. 保存结果到 JSON 文件
    
    重要：如果 browser_evaluate 返回的内容很长（超过2000字符），建议使用文件路径作为输入：
    - evaluate_result 参数可以是文件路径（如 "logs/browser_evaluate_result_20260114_141459_1.json"）
    - 工具会自动检测是否为文件路径，如果是则从文件读取完整内容
    - 这样可以避免 LLM 调用时参数被截断的问题
    - **注意**：logs 文件夹下可能有多个 json/txt 文件，必须指定完整的文件路径（包含文件名和时间戳），不能使用通配符
    
    Args:
        evaluate_result: browser_evaluate 工具返回的结果（字符串格式，可能是 JSON）或文件路径
                        如果输入是文件路径（以 "logs/" 开头或包含路径分隔符），工具会从文件读取完整内容
                        必须指定完整的文件路径，例如 "logs/browser_evaluate_result_20260114_141459_1.json"
        output_file: 输出文件路径（可选），如果提供则保存结果到该文件。如果输入超过2000字符且未指定此参数，会自动保存到logs目录
        extract_text_only: 是否只提取文本内容（True 只保存文本，False 保存完整结构）
        
    Returns:
        解析结果的描述信息，包括提取的文字内容摘要和保存的文件路径（如果指定）
    """
    try:
        # 步骤1: 检查输入是否为文件路径，如果是则从文件读取完整内容
        # 注意：logs 文件夹下可能有多个 json/txt 文件，需要精确匹配文件路径
        input_file_path = None
        if evaluate_result:
            # 更严格的文件路径检测逻辑：
            # 1. 必须是有效的文件路径格式（包含路径分隔符，或以 logs/ 开头）
            # 2. 文件必须实际存在
            # 3. 避免将普通字符串（如包含 .json 文本的 HTML）误判为文件路径
            potential_path = Path(evaluate_result)
            
            # 检查是否为文件路径格式：
            # - 以 logs/ 开头（相对路径）
            # - 包含路径分隔符（/ 或 \）
            # - 或者路径存在且是文件
            is_likely_file_path = (
                evaluate_result.startswith("logs/") or
                "/" in evaluate_result or
                "\\" in evaluate_result or
                (potential_path.exists() and potential_path.is_file())
            )
            
            if is_likely_file_path:
                try:
                    input_file_path = Path(evaluate_result)
                    # 严格检查：文件必须存在且是文件（不是目录）
                    if input_file_path.exists() and input_file_path.is_file():
                        logger.info(f"检测到文件路径输入，从文件读取完整内容: {input_file_path.absolute()}")
                        with open(input_file_path, 'r', encoding='utf-8') as f:
                            evaluate_result = f.read()
                        logger.info(f"从文件读取内容，长度: {len(evaluate_result)} 字符")
                    else:
                        # 文件不存在，尝试查找 logs 目录下最新的相关文件
                        if evaluate_result.startswith("logs/") or "logs/" in evaluate_result:
                            logs_dir = Path("logs")
                            if logs_dir.exists() and logs_dir.is_dir():
                                # 查找所有 browser_evaluate_result 文件
                                pattern = "browser_evaluate_result_*.json"
                                matching_files = sorted(logs_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
                                if matching_files:
                                    # 使用最新的文件
                                    latest_file = matching_files[0]
                                    logger.warning(f"指定的文件不存在: {evaluate_result}，使用最新的文件: {latest_file}")
                                    with open(latest_file, 'r', encoding='utf-8') as f:
                                        evaluate_result = f.read()
                                    logger.info(f"从最新文件读取内容，长度: {len(evaluate_result)} 字符")
                                else:
                                    error_msg = f"指定的文件不存在: {evaluate_result}，且 logs 目录下没有找到任何 browser_evaluate_result 文件"
                                    logger.error(error_msg)
                                    return f"❌ {error_msg}\n💡 提示: 请检查文件路径是否正确，或先调用 browser_evaluate 工具生成结果文件"
                            else:
                                error_msg = f"指定的文件不存在: {evaluate_result}，且 logs 目录不存在"
                                logger.error(error_msg)
                                return f"❌ {error_msg}\n💡 提示: 请检查文件路径是否正确"
                        else:
                            error_msg = f"指定的文件不存在: {evaluate_result}"
                            logger.error(error_msg)
                            return f"❌ {error_msg}\n💡 提示: 请检查文件路径是否正确，或先调用 browser_evaluate 工具生成结果文件"
                except Exception as e:
                    error_msg = f"尝试从文件读取失败: {e}"
                    logger.error(error_msg, exc_info=True)
                    return f"❌ {error_msg}\n💡 提示: 请检查文件路径是否正确，或直接传入 browser_evaluate 的结果内容"
        
        # 步骤2: 检查输入长度，如果太长且未指定输出文件，自动保存到临时文件
        input_length = len(evaluate_result) if evaluate_result else 0
        auto_saved = False
        auto_save_path = None
        
        if input_length > AUTO_SAVE_THRESHOLD and not output_file:
            # 自动生成临时文件路径
            logs_dir = Path("logs")
            logs_dir.mkdir(exist_ok=True)
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            auto_save_path = logs_dir / f"parsed_evaluate_result_{timestamp}.json"
            output_file = str(auto_save_path)
            auto_saved = True
            logger.info(f"输入内容过长 ({input_length} 字符)，自动保存完整内容到临时文件: {auto_save_path}")
        
        # 解析结果（完整解析，不截断）
        # 注意：这里传入的是完整的 evaluate_result，不会被截断
        parsed = _parse_evaluate_result(evaluate_result)
        
        # 验证解析结果
        parsed_text_length = len(parsed.get("text_content", ""))
        raw_result_length = len(str(parsed.get("raw_result", "")))
        logger.debug(f"解析完成 - 输入长度: {input_length} 字符, 提取文本长度: {parsed_text_length} 字符, 原始结果长度: {raw_result_length} 字符")
        
        # 步骤4: 准备保存的数据（完整数据，不截断）
        if extract_text_only:
            save_data = {
                "text_content": parsed["text_content"],  # 完整的提取文本，无长度限制
            }
        else:
            save_data = {
                "text_content": parsed["text_content"],  # 完整的提取文本，无长度限制
                "structured_data": parsed["structured_data"],  # 完整的结构化数据
                "raw_result": parsed["raw_result"],  # 完整的原始输入，无长度限制，不会被截断
            }
        
        # 步骤5: 保存到文件（完整保存，不截断）
        # 如果指定了输出文件（包括自动生成的临时文件），保存完整内容
        if output_file:
            output_path = Path(output_file)
            # 确保目录存在
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 保存为 JSON（完整内容，不截断）
            # 注意：这里保存的是完整内容，包括：
            # - text_content: 完整的提取文本（无长度限制，不会被截断）
            # - structured_data: 完整的结构化数据（如果存在，不会被截断）
            # - raw_result: 完整的原始输入（如果 extract_text_only=False，不会被截断）
            # json.dump 会完整保存所有内容，不会截断
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
            
            # 验证保存的数据大小
            total_size = len(json.dumps(save_data, ensure_ascii=False))
            logger.info(f"已保存完整解析结果到: {output_path}")
            logger.info(f"  - 输入长度: {input_length} 字符")
            logger.info(f"  - 提取文本长度: {len(parsed['text_content'])} 字符")
            logger.info(f"  - 原始结果长度: {raw_result_length} 字符")
            logger.info(f"  - 保存文件总大小: {total_size} 字符（完整保存，未截断）")
            
            # 生成返回信息（终端显示截断预览，完整内容已保存到文件）
            TERMINAL_PREVIEW_LENGTH = 100  # 终端预览只显示100字符
            text_preview = parsed["text_content"][:TERMINAL_PREVIEW_LENGTH] if parsed["text_content"] else "无文本内容"
            if len(parsed["text_content"]) > TERMINAL_PREVIEW_LENGTH:
                text_preview += "..."
            
            auto_save_note = "\n💡 提示: 由于输入内容较长，已自动保存到文件" if auto_saved else ""
            
            return (
                f"✅ 解析完成并已保存到文件\n"
                f"📁 文件路径: {output_path.absolute()}\n"
                f"📝 文本内容长度: {len(parsed['text_content'])} 字符\n"
                f"📄 内容预览（完整内容已保存到文件）: {text_preview}{auto_save_note}"
            )
        else:
            # 不保存文件，只返回解析结果摘要（终端显示截断预览）
            TERMINAL_PREVIEW_LENGTH = 100  # 终端预览只显示100字符
            text_preview = parsed["text_content"][:TERMINAL_PREVIEW_LENGTH] if parsed["text_content"] else "无文本内容"
            if len(parsed["text_content"]) > TERMINAL_PREVIEW_LENGTH:
                text_preview += "..."
            
            return (
                f"✅ 解析完成\n"
                f"📝 文本内容长度: {len(parsed['text_content'])} 字符\n"
                f"📄 内容预览: {text_preview}\n"
                f"💡 提示: 可以使用 output_file 参数保存完整结果到文件"
            )
    
    except Exception as e:
        error_msg = f"解析 browser_evaluate 结果时出错: {e}"
        logger.error(error_msg, exc_info=True)
        return f"❌ {error_msg}"


@tool
def extract_text_from_evaluate_result(
    evaluate_result: str,
    output_file: Optional[str] = None
) -> str:
    """
    从 browser_evaluate 结果中提取纯文本内容并保存到文件。
    
    这是 parse_browser_evaluate_result 的简化版本，专门用于提取文本内容。
    
    注意：如果输入内容超过2000字符，工具会自动保存到临时文件，避免超时。
    
    Args:
        evaluate_result: browser_evaluate 工具返回的结果
        output_file: 输出文件路径（可选），如果提供则保存文本内容到该文件。如果输入超过2000字符且未指定此参数，会自动保存到logs目录
        
    Returns:
        提取的文本内容摘要或保存结果
    """
    try:
        # 检查输入长度，如果太长且未指定输出文件，自动保存到临时文件
        input_length = len(evaluate_result) if evaluate_result else 0
        auto_saved = False
        auto_save_path = None
        
        if input_length > AUTO_SAVE_THRESHOLD and not output_file:
            # 自动生成临时文件路径
            logs_dir = Path("logs")
            logs_dir.mkdir(exist_ok=True)
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            auto_save_path = logs_dir / f"extracted_text_{timestamp}.txt"
            output_file = str(auto_save_path)
            auto_saved = True
            logger.info(f"输入内容过长 ({input_length} 字符)，自动保存完整文本内容到临时文件: {auto_save_path}")
        
        # 解析结果
        parsed = _parse_evaluate_result(evaluate_result)
        text_content = parsed["text_content"]
        
        if not text_content:
            return "⚠️ 未能从结果中提取到文本内容"
        
        # 如果指定了输出文件（包括自动生成的），保存到文件
        if output_file:
            output_path = Path(output_file)
            # 确保目录存在
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 保存为纯文本文件（完整内容，不截断）
            # 注意：这里保存的是完整的文本内容，无长度限制，不会截断
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(text_content)
            
            logger.info(f"已保存完整文本内容到: {output_path} (长度: {len(text_content)} 字符，完整保存，未截断)")
            
            # 终端预览只显示截断摘要（完整内容已保存到文件）
            TERMINAL_PREVIEW_LENGTH = 100  # 终端预览只显示100字符
            text_preview = text_content[:TERMINAL_PREVIEW_LENGTH]
            if len(text_content) > TERMINAL_PREVIEW_LENGTH:
                text_preview += "..."
            
            auto_save_note = "\n💡 提示: 由于输入内容较长，已自动保存到文件" if auto_saved else ""
            
            return (
                f"✅ 文本提取完成并已保存\n"
                f"📁 文件路径: {output_path.absolute()}\n"
                f"📝 文本长度: {len(text_content)} 字符\n"
                f"📄 内容预览（完整内容已保存到文件）: {text_preview}{auto_save_note}"
            )
        else:
            # 返回文本内容（终端显示截断预览）
            TERMINAL_PREVIEW_LENGTH = 100  # 终端预览只显示100字符
            text_preview = text_content[:TERMINAL_PREVIEW_LENGTH]
            if len(text_content) > TERMINAL_PREVIEW_LENGTH:
                text_preview += "..."
            
            return (
                f"✅ 文本提取完成\n"
                f"📝 文本长度: {len(text_content)} 字符\n"
                f"📄 内容预览: {text_preview}\n"
                f"💡 提示: 可以使用 output_file 参数保存完整内容到文件"
            )
    
    except Exception as e:
        error_msg = f"提取文本时出错: {e}"
        logger.error(error_msg, exc_info=True)
        return f"❌ {error_msg}"


def get_browser_evaluate_parser_tools() -> list[BaseTool]:
    """
    获取所有 browser_evaluate 解析工具
    
    Returns:
        工具列表
    """
    # return [
    #     parse_browser_evaluate_result,
    #     extract_text_from_evaluate_result,
    # ]
    # 返回空列表，不使用这些工具
    return []

