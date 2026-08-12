"""
browser-use 风格 Agent 输出模型

使用 Pydantic 严格定义 action 的 schema，确保 LLM 输出与 MCP 工具参数一致。
- FillParams 仅定义 value（禁止 text），从源头约束 LLM 输出
- 各 action 使用独立模型，with_structured_output 会生成精确的 JSON schema 指导 LLM
"""
import json
from typing import List, Optional, Dict, Any, Union, get_args
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


# ==================== Action 参数模型（严格对应 MCP 工具签名） ====================

class NavigateParams(BaseModel):
    url: str = Field(description="完整 URL，如 https://www.baidu.com")
    model_config = ConfigDict(extra="forbid")


class NewPageParams(BaseModel):
    url: str = Field(description="完整 URL")
    model_config = ConfigDict(extra="forbid")


class ClickParams(BaseModel):
    uid: str = Field(description="元素 uid，从页面快照获取，格式如 2_5")
    dbl_click: bool = Field(default=False, description="是否双击")
    model_config = ConfigDict(extra="forbid")


class FillParams(BaseModel):
    """browser_fill 参数：使用 value（与 MCP 工具一致），兼容 LLM 误输出的 text"""
    uid: str = Field(description="输入框元素 uid")
    value: str = Field(description="要填写的文本内容")
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _accept_text_as_value(cls, data: Any) -> Any:
        """LLM 输出 {'uid': 'x', 'text': 'y'} 时，将 text 转为 value"""
        if isinstance(data, dict) and "text" in data and "value" not in data:
            data = dict(data)
            data["value"] = data.pop("text", "")
        return data


class SnapshotParams(BaseModel):
    verbose: bool = Field(default=False, description="是否包含完整 a11y 树")
    model_config = ConfigDict(extra="forbid")


class ListPagesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SelectPageParams(BaseModel):
    pageId: int = Field(description="要切换的页面 pageId")
    model_config = ConfigDict(extra="forbid")


class PressKeyParams(BaseModel):
    key: str = Field(description="按键名称，如 Enter、Control+a、Delete")
    model_config = ConfigDict(extra="forbid")


class DoneParams(BaseModel):
    text: str = Field(description="任务完成时的结果描述或反馈")
    success: bool = Field(default=True, description="任务是否成功完成")
    model_config = ConfigDict(extra="forbid")


class ExtractDcdByUrlParams(BaseModel):
    url: str = Field(
        description="懂车帝帖子详情页 URL，如 https://www.dongchedi.com/ugc/article/xxx"
    )
    model_config = ConfigDict(extra="forbid")


class ExtractAutohomeParams(BaseModel):
    url: str = Field(description="汽车之家帖子详情页 URL，如 https://club.autohome.com.cn/bbs/thread/...")
    model_config = ConfigDict(extra="forbid")


class FilterVocParams(BaseModel):
    input_file: str = Field(description="JSON 文件路径，通常是 extract 工具保存的文件")
    model_config = ConfigDict(extra="forbid")


class AnalyzeVocParams(BaseModel):
    input_file: str = Field(description="JSON 文件路径")
    model_config = ConfigDict(extra="forbid")


class StoreVocFromJsonFileParams(BaseModel):
    input_file: str = Field(
        description="JSON 文件路径，通常为 browser_extract_* 返回的 file_path"
    )
    platform: Optional[str] = Field(
        default=None,
        description="平台覆盖，如 autohome、dongchedi；省略则自动识别",
    )
    model_config = ConfigDict(extra="forbid")


class VocMongoPingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FetchAndDownloadDouyinVideoParams(BaseModel):
    url: str = Field(description="抖音视频详情页 URL，如 https://www.douyin.com/jingxuan?modal_id=xxx 或视频分享链接")
    initial_wait_ms: Optional[int] = Field(default=None, description="导航后等待页面加载时间（毫秒），可选")
    play_wait_ms: Optional[int] = Field(default=None, description="播放后等待请求出现时间（毫秒），可选")
    network_limit: Optional[int] = Field(default=None, description="网络钩子回退检查的最近请求数，可选")
    include_all_videos: Optional[bool] = Field(default=None, description="是否包含所有 video 元素 URL，可选")
    referer: Optional[str] = Field(default=None, description="Referer 请求头，可选")
    model_config = ConfigDict(extra="forbid")


class AnalyzeVideoParams(BaseModel):
    video_file: str = Field(description="视频文件路径，通常来自 browser_fetch_and_download_douyin_video 返回的 file_path")
    question: Optional[str] = Field(default=None, description="对提取方式的说明，可选；省略时输出连贯文章")
    slice_seconds: int = Field(default=0, description="按秒切片处理，0 表示不切片")
    model_config = ConfigDict(extra="forbid")


class VisionClickCoordinate(BaseModel):
    """
    视觉点击坐标模型：约束 GUI-Owl 等视觉模型的输出格式。

    目标格式为:
        {"x": 123, "y": 456}

    兼容以下常见误输出形式（通过前置归一化）：
        {"x": [123, 456]}
        {"xy": [123, 456]}
        [123, 456]
    """

    x: int = Field(description="点击位置的 X 坐标（像素，从截图左上角开始）")
    y: int = Field(description="点击位置的 Y 坐标（像素，从截图左上角开始）")
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _normalize_variants(cls, data: Any) -> Any:
        # 直接传 [x, y]
        if isinstance(data, list) and len(data) >= 2:
            return {"x": data[0], "y": data[1]}

        if isinstance(data, dict):
            d = dict(data)

            # 兼容 {"x": [x, y]}：取列表前两个值作为 (x, y)
            x_val = d.get("x")
            if isinstance(x_val, list) and len(x_val) >= 2:
                d["x"] = x_val[0]
                d.setdefault("y", x_val[1])

            # 兼容 {"xy": [x, y]}：映射到 x, y
            if "xy" in d and "x" not in d and "y" not in d:
                xy = d.get("xy")
                if isinstance(xy, list) and len(xy) >= 2:
                    d["x"], d["y"] = xy[0], xy[1]

            return d

        return data


# ==================== 单动作模型（每项对应一个工具） ====================

class BrowserNavigateAction(BaseModel):
    browser_navigate: NavigateParams

    @field_validator("browser_navigate", mode="before")
    @classmethod
    def _coerce_url_to_params(cls, v: Any) -> Any:
        """LLM 可能输出 browser_navigate: "url" 而非 {"url": "..."}，自动规范化"""
        if isinstance(v, str):
            return {"url": v}
        return v


class BrowserNewPageAction(BaseModel):
    browser_new_page: NewPageParams

    @field_validator("browser_new_page", mode="before")
    @classmethod
    def _coerce_url_to_params(cls, v: Any) -> Any:
        """LLM 可能输出 browser_new_page: "url" 而非 {"url": "..."}，自动规范化"""
        if isinstance(v, str):
            return {"url": v}
        return v


class BrowserClickAction(BaseModel):
    browser_click: ClickParams


class BrowserFillAction(BaseModel):
    browser_fill: FillParams


class BrowserSnapshotAction(BaseModel):
    browser_snapshot: SnapshotParams


class BrowserListPagesAction(BaseModel):
    browser_list_pages: ListPagesParams


class BrowserSelectPageAction(BaseModel):
    browser_select_page: SelectPageParams


class BrowserPressKeyAction(BaseModel):
    browser_press_key: PressKeyParams


class BrowserExtractDcdPostDetailAction(BaseModel):
    browser_extract_dcd_post_detail: ExtractDcdByUrlParams


class BrowserExtractAutohomePostDetailAction(BaseModel):
    browser_extract_autohome_post_detail: ExtractAutohomeParams


class BrowserFilterVocAction(BaseModel):
    browser_filter_voc: FilterVocParams


class BrowserAnalyzeVocAction(BaseModel):
    browser_analyze_voc: AnalyzeVocParams


class BrowserVocStoreFromJsonFileAction(BaseModel):
    browser_voc_store_from_json_file: StoreVocFromJsonFileParams


class BrowserVocMongoPingAction(BaseModel):
    browser_voc_mongo_ping: VocMongoPingParams


class BrowserFetchAndDownloadDouyinVideoAction(BaseModel):
    browser_fetch_and_download_douyin_video: FetchAndDownloadDouyinVideoParams


class BrowserAnalyzeVideoAction(BaseModel):
    browser_analyze_video: AnalyzeVideoParams


class BrowserOtherToolAction(BaseModel):
    """未单独列出的工具调用：键为工具名、值为参数字典。新增工具只需在 chrome_devtools 注册，无需改此处。"""

    model_config = ConfigDict(extra="allow")


# Action 联合类型：先匹配上述具体类型，其余任意工具名+参数由 BrowserOtherToolAction 兜底，新增工具无需改 Union
BrowserUseAction = Union[
    BrowserNavigateAction,
    BrowserNewPageAction,
    BrowserClickAction,
    BrowserFillAction,
    BrowserSnapshotAction,
    BrowserListPagesAction,
    BrowserSelectPageAction,
    BrowserPressKeyAction,
    BrowserExtractDcdPostDetailAction,
    BrowserExtractAutohomePostDetailAction,
    BrowserFilterVocAction,
    BrowserAnalyzeVocAction,
    BrowserVocStoreFromJsonFileAction,
    BrowserVocMongoPingAction,
    BrowserFetchAndDownloadDouyinVideoAction,
    BrowserAnalyzeVideoAction,
    BrowserOtherToolAction,  # 兜底：browser_screenshot、browser_click_at、browser_close_page 等任意工具
]


def get_uid_dependent_tool_names() -> frozenset[str]:
    """从 schema 推导：参数中含 uid 的 action 对应工具名（依赖当前快照，操作后快照会变，同轮仅执行一个）。"""
    names: set[str] = set()
    for action_cls in get_args(BrowserUseAction):
        if not hasattr(action_cls, "model_fields"):
            continue
        for fname, finfo in action_cls.model_fields.items():
            ann = getattr(finfo, "annotation", None)
            if ann is not None and hasattr(ann, "model_fields") and "uid" in ann.model_fields:
                names.add(fname)
    return frozenset(names)


# ==================== Agent 输出模型 ====================

class BrowserUseAgentOutput(BaseModel):
    """browser-use 风格 Agent 输出（Pydantic 严格校验）"""
    evaluation_previous_goal: str = Field(
        default="",
        description="对上一步目标的评估：成功/失败/不确定，一句话说明"
    )
    memory: str = Field(
        default="",
        description="本步及整体进度记忆，1-3 句，用于后续步骤追踪"
    )
    next_goal: str = Field(
        default="",
        description="下一步立即目标，一句话"
    )
    action: List[BrowserUseAction] = Field(
        default_factory=list,
        description="动作列表，每项为 {tool_name: {params}}；任务完成时填 task_complete 并可将 action 置为 []",
        min_length=0
    )
    task_complete: Optional[DoneParams] = Field(
        default=None,
        description="任务完成时填写，含 text（结果描述）与 success；填写后本步不调用任何工具，直接结束"
    )

    @model_validator(mode="before")
    @classmethod
    def _action_empty_when_task_complete(cls, data: Any) -> Any:
        """填写 task_complete 时强制 action 为 []，避免 LLM 误输出 action: [{}] 等导致 Union 校验失败。"""
        if isinstance(data, dict) and data.get("task_complete") is not None:
            data = dict(data)
            data["action"] = []
        return data


# ==================== Reflect 节点：任务完成判断输出 ====================

class TaskCompletionJudgmentOutput(BaseModel):
    """任务完成判断输出模型（Reflect 节点解析 LLM 输出）"""
    task_status: str = Field(description="任务状态：completed/not_completed/failed")
    judgment_reasoning: str = Field(
        description="判断依据说明，包括原始任务要求、已执行的操作、缺失的操作、页面状态等"
    )
    final_result: Optional[str] = Field(
        default=None,
        description="如果任务完成，给出结果描述；否则说明原因和缺失操作"
    )
    needs_replan: bool = Field(description="是否需要重新规划")
    missing_operations: Optional[List[str]] = Field(
        default=None,
        description="缺失的操作列表，如果没有则为None"
    )


# ==================== 提示词占位符：从 Schema 生成格式 ====================

def _params_to_json_example(
    model: type[BaseModel],
    placeholders: Optional[Dict[str, Any]] = None,
) -> str:
    """从 Pydantic 模型生成 JSON 示例字符串，优先使用 placeholders"""
    schema = model.model_json_schema()
    props = schema.get("properties", {})
    if not props:
        return "{}"
    parts = []
    for k, v in props.items():
        if placeholders is not None and k in placeholders:
            pv = placeholders[k]
            if isinstance(pv, bool):
                parts.append(f'"{k}": {str(pv).lower()}')
            elif isinstance(pv, (int, float)):
                parts.append(f'"{k}": {pv}')
            elif isinstance(pv, list):
                parts.append(f'"{k}": {json.dumps(pv, ensure_ascii=False)}')
            else:
                parts.append(f'"{k}": "{pv}"')
        else:
            t = v.get("type", "string")
            if t == "string":
                parts.append(f'"{k}": "..."')
            elif t == "boolean":
                parts.append(f'"{k}": false')
            elif t == "integer":
                parts.append(f'"{k}": 0')
            elif t == "array":
                parts.append(f'"{k}": ["..."]')
            else:
                parts.append(f'"{k}": "..."')
    return "{" + ", ".join(parts) + "}"


def get_task_complete_format() -> str:
    """从 DoneParams 生成 task_complete 的格式说明，供提示词占位符 {task_complete_format} 与 get_browser_tools_format 使用。"""
    done_ex = _params_to_json_example(DoneParams, {"text": "结果描述", "success": True})
    return (
        f"task_complete 是你终止并向用户汇报结果的唯一方式（不是工具，是输出字段）。"
        f"在 JSON 根级别填 task_complete: {done_ex}，并将 action 置为 []；"
        "task_complete 与 action 同级，禁止写在 action 数组内，勿再调用工具。"
    )


def get_browser_tools_format() -> str:
    """
    从 Pydantic Schema 生成工具格式描述（用于提示词占位符 {tools_format}）
    
    Returns:
        格式化的工具列表文本
    """
    lines = ["**可用工具**（参数不可变）："]
    nav_ex = _params_to_json_example(NavigateParams, {"url": "完整URL"})
    new_ex = _params_to_json_example(NewPageParams, {"url": "完整URL"})
    lines.append(f"- browser_navigate({nav_ex})、browser_new_page({new_ex})")
    click_ex = _params_to_json_example(ClickParams, {"uid": "快照中的uid", "dbl_click": False})
    fill_ex = _params_to_json_example(FillParams, {"uid": "快照中的uid", "value": "文本"})
    lines.append(f"- browser_click({click_ex})、browser_fill({fill_ex})")
    snap_ex = _params_to_json_example(SnapshotParams, {"verbose": False})
    select_ex = _params_to_json_example(SelectPageParams, {"pageId": 0})
    lines.append(f"- browser_snapshot({snap_ex})、browser_list_pages(无参数)、browser_select_page({select_ex})")
    key_ex = _params_to_json_example(PressKeyParams, {"key": "Enter|Control+a|Delete|..."})
    lines.append(f"- browser_press_key({key_ex})")
    lines.append(f"- **任务完成**：{get_task_complete_format()}")
    dcd_ex = _params_to_json_example(ExtractDcdByUrlParams, {"url": "当前页面URL"})
    ah_ex = _params_to_json_example(ExtractAutohomeParams, {"url": "当前页面URL"})
    voc_ex = _params_to_json_example(AnalyzeVocParams, {"input_file": "JSON路径"})
    store_ex = _params_to_json_example(
        StoreVocFromJsonFileParams,
        {"input_file": "extract 返回的 file_path", "platform": "autohome"},
    )
    lines.append(
        f"- **任务要求保存 JSON 或 VOC 分析时**：browser_extract_dcd_post_detail({dcd_ex})、"
        f"browser_extract_autohome_post_detail({ah_ex})、browser_analyze_voc({voc_ex})。"
        "车家号 info 页用 browser_extract_autohome_chejiahao_info（url=车家号链接）；"
        "懂车帝视频页用 browser_extract_dcd_video（url=视频页链接）。"
        "以上 JSON 均写入 downloads/ 下固定子目录。禁止仅用 snapshot 代替。"
    )
    lines.append(
        f"- **任务要求保存到数据库 / MongoDB 入库时**：先 extract 得到 JSON 的 file_path，"
        f"再调用 browser_voc_store_from_json_file({store_ex})。"
        "可用 browser_voc_mongo_ping() 检测连接。需配置 MCP 环境变量 MONGO_URI、MONGO_DB。"
    )
    lines.append(
        "  **extract 工具 url 必须为真实 URL**（禁止占位符）："
        "(1) 若当前已是帖子详情页，用 browser_state 的「当前URL」；"
        "(2) 若在搜索/列表页：先点击进入某条详情页后再 extract；或从快照中直接取“详情页链接”的真实 URL 作为 extract 的 url（前提：该 URL 确实是详情页）。"
        "**禁止**使用示例或占位符（如 /ugc/article/xxx、懂车帝帖子URL、汽车之家帖子URL 等），必须使用实际页面或快照中的真实 URL。"
    )
    fetch_douyin_ex = _params_to_json_example(FetchAndDownloadDouyinVideoParams, {"url": "抖音视频详情页或 jingxuan?modal_id=xxx 的 URL"})
    analyze_video_ex = _params_to_json_example(AnalyzeVideoParams, {"video_file": "下载后的视频路径，如 file_path 返回值"})
    lines.append(
        f"- **任务要求下载抖音视频或对视频做画面文字提取时**："
        f"browser_fetch_and_download_douyin_video({fetch_douyin_ex}) 下载视频，"
        f"再用 browser_analyze_video({analyze_video_ex}) 从视频画面提取字幕/标牌等文字。"
        "browser_analyze_video 的 video_file 填 browser_fetch_and_download_douyin_video 返回的 file_path。"
    )
    return "\n".join(lines)


def action_dict_to_tool_call(action) -> Optional[Dict[str, Any]]:
    """将 action（Pydantic 模型或 dict）转换为 LangChain tool_call 格式（通用，无工具特定逻辑）"""
    if action is None:
        return None
    d = action.model_dump() if hasattr(action, "model_dump") else (action if isinstance(action, dict) else None)
    if not d or not isinstance(d, dict):
        return None
    tool_name = next(iter(d.keys()), None)
    if not tool_name:
        return None
    args = d[tool_name] if isinstance(d.get(tool_name), dict) else {}
    return {"name": tool_name, "args": args}
