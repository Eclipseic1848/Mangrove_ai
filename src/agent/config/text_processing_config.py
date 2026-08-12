"""
文本处理配置

定义文本截取和格式化的配置参数，避免硬编码。
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TextProcessingConfig:
    """文本处理配置类
    
    所有文本截取相关的长度限制和阈值都在这里定义，便于统一管理和调整。
    """
    # 页面快照截取配置
    snapshot_max_length_reflection: int = 1500  # 反思节点中快照的最大长度
    snapshot_max_length_judge: int = 1500       # 任务完成判断节点中快照的最大长度
    snapshot_max_length_execution: int = 6000   # 执行节点中关键词提取的默认最大长度（关键词邻域保留）
    # 执行节点快照总长度上限（字符），用于控制 LLM 上下文不超 150000 tokens。关键词内容优先保留。
    # 说明：执行节点提示词中会包含「之前步骤结果 + 页面快照」，这里将页面快照硬性压到 ~8000 字符量级，
    # 结合系统提示和步骤文案，整体远低于 176k tokens 的模型上限。
    max_execution_snapshot_chars: int = 8000  # 执行阶段用于元素定位的页面快照总上限
    snapshot_max_length_replan_context: int = 800  # 重新规划上下文中快照的最大长度
    # 重规划时「之前的执行情况」里每步 result 的最大字符数（避免整页快照塞入导致 token 超限）
    max_replan_step_result_chars: int = 800
    # 规划阶段页面快照最大字符数（browser-use 风格：DOM/元素列表 40k 上限，此处用 8k 控制 token）
    max_plan_snapshot_chars: int = 8000
    
    # 关键信息提取配置
    url_search_window: int = 5000    # URL搜索窗口大小（在前N字符中查找）
    title_search_window: int = 2000  # 标题搜索窗口大小
    first_line_max_length: int = 1000 # 第一行最大保留长度
    max_urls_to_extract: int = 5     # 最多提取的URL数量
    max_urls_to_check: int = 10       # 最多检查的URL数量
    
    # 文本截取配置
    text_max_length_reflection: int = 500  # 反思节点中文本的最大长度
    text_max_length_judge: int = 1000      # 任务完成判断节点中文本的最大长度
    
    # 其他配置
    min_key_info_length: int = 200  # 关键信息最小保留长度
    # 关键词提取无匹配时，若结果长度低于此值则回退到完整快照（避免只返回 header）
    keyword_extract_fallback_min_length: int = 200
    separator_reserve_length: int = 100  # 分隔符和省略号预留长度
    
    # 计算配置（基于最大长度的比例）
    head_tail_ratio: float = 0.7  # 开头和结尾的比例（0.5表示各占一半）
    
    # browser-use 风格优化：单行/单元素文本截断
    max_line_length: int = 150  # 单行最大字符数（参考 browser-use cap_text_length=100，此处略放宽）
    # 可交互元素优先：快照超过此长度且无关键词时，仅保留 link/button/textbox 等可交互行
    interactive_filter_threshold: int = 12000
    
    # 显示和预览配置（用于终端输出和错误信息）
    error_info_preview_length: int = 1000  # 错误信息预览长度
    error_detail_length: int = 500  # 错误详情长度
    step_description_preview_length: int = 1000  # 步骤描述预览长度
    step_result_preview_length: int = 1000  # 步骤结果预览长度
    terminal_output_preview_length: int = 500  # 终端输出预览长度
    judgment_content_preview_length: int = 500  # 判断内容预览长度
    text_match_check_length: int = 500  # 文本匹配检查长度（用于避免误匹配）
    
    def get_snapshot_max_length(self, context: str) -> int:
        """
        根据上下文获取对应的快照最大长度
        
        Args:
            context: 上下文类型，可选值: 'reflection', 'judge', 'execution', 'replan'
        
        Returns:
            对应的最大长度值
        """
        mapping = {
            'reflection': self.snapshot_max_length_reflection,
            'judge': self.snapshot_max_length_judge,
            'execution': self.max_execution_snapshot_chars,  # 执行节点用总上限，控制 token
            'execution_keyword': self.snapshot_max_length_execution,  # 关键词提取时的单次输出上限
            'replan': self.snapshot_max_length_replan_context,
            'plan': self.max_plan_snapshot_chars,  # 规划阶段页面快照上限（browser-use 风格）
        }
        return mapping.get(context, self.snapshot_max_length_reflection)
    
    def get_text_max_length(self, context: str) -> int:
        """
        根据上下文获取对应的文本最大长度
        
        Args:
            context: 上下文类型，可选值: 'reflection', 'judge'
        
        Returns:
            对应的最大长度值
        """
        mapping = {
            'reflection': self.text_max_length_reflection,
            'judge': self.text_max_length_judge,
        }
        return mapping.get(context, self.text_max_length_reflection)


# 全局默认配置实例
default_text_config = TextProcessingConfig()

