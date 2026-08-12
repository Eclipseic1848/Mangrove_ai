# -*- coding: utf-8 -*-
"""用官方 Label Studio SDK 对接批量文档复核。"""
from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import httpx

from src.data_prep.document_models import ExtractedField, ReviewTask


LABEL_STUDIO_DOCUMENT_REVIEW_CONFIG = """
<View>
  <Header value="字段：$field_name"/>
  <Text name="context" value="$context"/>
  <HyperText name="evidence" value="$evidence_html"/>
  <Choices name="decision" toName="evidence" choice="single" required="true">
    <Choice value="接受候选"/>
    <Choice value="使用修订值"/>
    <Choice value="标记未找到"/>
  </Choices>
  <TextArea name="replacement" toName="evidence"
            placeholder="选择“使用修订值”时填写"/>
  <TextArea name="note" toName="evidence" placeholder="复核说明（可选）"/>
</View>
""".strip()


@dataclass(frozen=True)
class LabelStudioDecision:
    review_task_id: str
    decision: str
    candidate_index: int | None = None
    value: Any = None
    note: str | None = None
    annotation_id: int | str | None = None


def _field_map(
    fields: Sequence[ExtractedField | Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        str(item.name if isinstance(item, ExtractedField) else item.get("name")): (
            item.model_dump(mode="json")
            if isinstance(item, ExtractedField)
            else dict(item)
        )
        for item in fields
    }


def _review_dict(review: ReviewTask | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(review, ReviewTask):
        return review.model_dump(mode="json")
    return dict(review)


def build_label_studio_tasks(
    mangrove_task_id: str,
    review_tasks: Sequence[ReviewTask | Mapping[str, Any]],
    fields: Sequence[ExtractedField | Mapping[str, Any]],
    *,
    document_urls: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """把标准 ReviewTask 转为 Label Studio 任务和模型预测。"""
    by_field = _field_map(fields)
    urls = document_urls or {}
    tasks: list[dict[str, Any]] = []
    for review_item in review_tasks:
        review = _review_dict(review_item)
        field = by_field.get(str(review.get("field_name"))) or {}
        candidates = list(review.get("candidates") or [])
        candidate = candidates[0] if candidates else {}
        evidence_refs = list(field.get("evidence_refs") or [])
        evidence_lines = [
            (
                f"第 {ref.get('page')} 页｜{ref.get('quote') or ''}"
                f"｜置信度 {float(ref.get('confidence') or 0):.3f}"
            )
            for ref in evidence_refs
        ]
        evidence_html = "<br/>".join(
            html.escape(line)
            for line in evidence_lines
        ) or "无已验证证据，请核对原文。"
        suggested_value = candidate.get("value", field.get("value"))
        prediction_result = []
        if suggested_value is not None:
            prediction_result.append({
                "from_name": "replacement",
                "to_name": "evidence",
                "type": "textarea",
                "value": {"text": [str(suggested_value)]},
            })
        tasks.append({
            "data": {
                "field_name": review.get("field_name"),
                "context": (
                    f"页码：{review.get('page')}；"
                    f"原因：{'；'.join(review.get('reasons') or [])}；"
                    f"候选值：{suggested_value if suggested_value is not None else '无'}"
                ),
                "evidence_html": evidence_html,
                "document_url": urls.get(str(review.get("artifact_id")), ""),
                "artifact_id": review.get("artifact_id"),
                "page": review.get("page"),
            },
            "meta": {
                "mangrove_task_id": mangrove_task_id,
                "mangrove_review_task_id": review.get("task_id"),
            },
            "predictions": [{
                "model_version": "mangrove-document-extraction-v1",
                "score": float(candidate.get("confidence") or 0),
                "result": prediction_result,
            }],
        })
    return tasks


def _result_value(
    results: Sequence[Mapping[str, Any]],
    from_name: str,
) -> Any:
    for result in results:
        if result.get("from_name") != from_name:
            continue
        value = result.get("value") or {}
        if "choices" in value:
            choices = value.get("choices") or []
            return choices[0] if choices else None
        if "text" in value:
            texts = value.get("text") or []
            return texts[0] if texts else None
    return None


def parse_label_studio_decisions(
    exported_tasks: Sequence[Mapping[str, Any]],
) -> list[LabelStudioDecision]:
    """读取 Label Studio 导出结果，每个复核项只保留最新完成标注。"""
    latest: dict[str, tuple[str, LabelStudioDecision]] = {}
    decision_map = {
        "接受候选": "accept_candidate",
        "使用修订值": "replace",
        "标记未找到": "mark_not_found",
    }
    for task in exported_tasks:
        review_task_id = str(
            (task.get("meta") or {}).get("mangrove_review_task_id") or ""
        )
        if not review_task_id:
            continue
        for annotation in task.get("annotations") or []:
            if annotation.get("was_cancelled"):
                continue
            results = annotation.get("result") or []
            raw_decision = _result_value(results, "decision")
            decision = decision_map.get(str(raw_decision))
            if not decision:
                continue
            replacement = _result_value(results, "replacement")
            if decision == "replace" and (
                replacement is None or not str(replacement).strip()
            ):
                raise ValueError(f"{review_task_id} 选择了修订值但没有填写内容")
            decided = LabelStudioDecision(
                review_task_id=review_task_id,
                decision=decision,
                candidate_index=0 if decision == "accept_candidate" else None,
                value=replacement if decision == "replace" else None,
                note=_result_value(results, "note"),
                annotation_id=annotation.get("id"),
            )
            updated_at = str(
                annotation.get("updated_at")
                or annotation.get("created_at")
                or ""
            )
            previous = latest.get(review_task_id)
            if previous is None or updated_at >= previous[0]:
                latest[review_task_id] = (updated_at, decided)
    return [item[1] for _, item in sorted(latest.items())]


class LabelStudioReviewClient:
    """延迟加载官方 SDK；未启用 Label Studio 时不增加主链启动成本。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        project_id: int,
        timeout_seconds: float = 60,
    ) -> None:
        if not base_url or not api_key or project_id <= 0:
            raise ValueError("Label Studio 地址、API Key 和 project_id 必须完整配置")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.project_id = project_id
        self.timeout_seconds = timeout_seconds

    def import_tasks(self, tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        try:
            from label_studio_sdk import LabelStudio
        except ImportError as exc:
            raise RuntimeError(
                "缺少 Label Studio SDK，请安装 requirements-label-studio.txt"
            ) from exc
        http_client = httpx.Client(
            trust_env=False,
            timeout=self.timeout_seconds,
            follow_redirects=True,
        )
        try:
            client = LabelStudio(
                base_url=self.base_url,
                api_key=self.api_key,
                httpx_client=http_client,
            )
            response = client.projects.import_tasks(
                id=self.project_id,
                request=list(tasks),
                return_task_ids=True,
            )
            if hasattr(response, "model_dump"):
                return response.model_dump(mode="json")
            if isinstance(response, Mapping):
                return dict(response)
            return {"result": str(response)}
        finally:
            http_client.close()
