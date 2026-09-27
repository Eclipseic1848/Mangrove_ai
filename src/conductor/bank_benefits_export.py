"""从同一场景快照导出可核对文件；此处不发布正式交付。"""
from __future__ import annotations

import json
import re
from pathlib import Path
import zipfile

import xlsxwriter

from src.data_prep.artifact_store import ArtifactStore
from .collection_results import collection_outcome, verified_image_bytes


def export_snapshot(state):
    snapshot = state.get("evidence_collection") or state["bank_collection"]
    outcome = collection_outcome(snapshot)
    snapshot = {**snapshot, "outcome": outcome,
                "result_view": {"count_unit": "note", "source_ids": [
                    note.get("metadata", {}).get("note_id") for note in snapshot["candidates"]
                    if note["status"] == "selected"]}}
    store = ArtifactStore()
    task_id = state["task_id"]
    attempt = state.get("collection_output_attempt")
    if attempt and not re.fullmatch(r"[0-9a-f]{32}", attempt):
        raise ValueError("恢复快照标识无效")
    suffix = "-" + attempt if attempt else ""
    root = store.task_dir(task_id)
    root.mkdir(parents=True, exist_ok=True)
    for note in snapshot["candidates"]:
        for image in note.get("images", []):
            verified_image_bytes(store, task_id, image)
    json_path = store.resolve_path(str(store.write_json(task_id, f"data{suffix}.json", snapshot)))
    notes, benefits, audit, comments, images = [], [], [], [], []
    for note in snapshot["candidates"]:
        meta = note.get("metadata") or {}
        assessment = note.get("assessment") or {}
        note_id = meta.get("note_id")
        notes.append({"note_id": note_id, "group": note.get("group", note.get("bank")), "status": note["status"],
                      "reason": note.get("reason"), "note_url": note.get("url"), "title": note.get("title"),
                      "content": note.get("content"),
                      **{key: value for key, value in meta.items() if key not in {"comments", "raw_record", "identity_verified"}},
                      **{key: value for key, value in assessment.items() if key != "benefits"}})
        for index, benefit in enumerate(assessment.get("records", assessment.get("benefits")) or [], 1):
            row = {**benefit, "note_id": note_id, "benefit_index": index,
                   "source_status": note["status"], "exclusion_reason": note.get("reason"),
                   "review_required": True}
            audit.append(row)
            if note["status"] == "selected":
                benefits.append(row)
        parent = {"note_id": note_id, "source_status": note["status"], "exclusion_reason": note.get("reason")}
        comments.extend({**comment, **parent} for comment in meta.get("comments") or [])
        images.extend({**image, **parent} for image in note.get("images") or [])
    explanations = [
        {"category": "任务", "key": "query", "value": snapshot["query"]},
        {"category": "任务", "key": "scope", "value": snapshot["scope"]},
        {"category": "状态", "key": "delivery", "value": "采集快照，未经用户确认，不是正式交付"},
        {"category": "状态", "key": "outcome", "value": outcome},
        *({"category": "分组执行", "key": group.get("name", group.get("bank")), "value": group}
          for group in snapshot.get("groups", snapshot.get("banks", []))),
    ]
    path = root / f"data{suffix}.xlsx"
    with xlsxwriter.Workbook(path, {"strings_to_formulas": False, "strings_to_urls": False}) as workbook:
        long_texts = []
        for title, rows, defaults in (
            ("笔记清单", notes, ["note_id", "status", "reason", "content"]),
            ("结构化明细" if state.get("evidence_collection") else "权益明细", benefits, ["note_id", "evidence", "missing_reasons"]),
            ("候选明细审计", audit, ["note_id", "source_status", "exclusion_reason", "evidence"]),
            ("评论", comments, ["note_id", "comment_id", "parent_comment_id", "content"]),
            ("图片识别", images, ["note_id", "image_index", "status", "reason", "text", "elements"]),
            ("执行说明", explanations, ["category", "key", "value"]),
        ):
            columns = list(dict.fromkeys(key for row in rows for key in row)) or defaults
            sheet = workbook.add_worksheet(title)
            sheet.write_row(0, 0, columns)
            sheet.freeze_panes(1, 0)
            sheet.set_column(0, len(columns) - 1, 22)
            for row_index, row in enumerate(rows, 1):
                for column_index, key in enumerate(columns):
                    value = row.get(key)
                    if value is None:
                        continue
                    if isinstance(value, (list, dict)):
                        value = json.dumps(value, ensure_ascii=False)
                    if isinstance(value, str) and len(value.encode("utf-16-le")) > 65000:
                        identity = f"{title}:{row_index}:{key}"
                        long_texts.extend((identity, index // 15000 + 1, value[index:index + 15000])
                                          for index in range(0, len(value), 15000))
                        value = f"完整内容见长文本：{identity}"
                    sheet.write(row_index, column_index, value)
            if rows:
                sheet.autofilter(0, 0, len(rows), len(columns) - 1)
        if long_texts:
            sheet = workbook.add_worksheet("长文本")
            sheet.write_row(0, 0, ["来源单元格", "分段序号", "原文"])
            for index, row in enumerate(long_texts, 1):
                sheet.write_row(index, 0, row)
    bundle_path = root / f"evidence{suffix}.zip"
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(json_path, "data.json")
        added = set()
        for image in images:
            if not image.get("artifact_path"):
                continue
            source = store.resolve_path(image["artifact_path"])
            if not source.resolve().is_relative_to(root.resolve()) or source.is_symlink():
                raise ValueError("图片证据越过当前任务目录")
            name = "images/" + source.name
            if name not in added:
                bundle.writestr(name, verified_image_bytes(store, task_id, image))
                added.add(name)
    count = sum(note["status"] == "selected" for note in snapshot["candidates"])
    return {"outputs": {"json": str(json_path), "xlsx": str(path), "evidence_zip": str(bundle_path)},
            "error": None if outcome["ready_for_review"] else outcome["message"],
            "collection_outcome": outcome,
            "reply": outcome["message"] + f"\n本次保留 {count} 篇入选笔记，快照可供核对，尚未正式交付。\n"
                     + "\n".join(state.get("collector_notes") or []),
            "grade": {"level": "待人工核对" if outcome["ready_for_review"] else "未完成", "score": None, "items": count, "collector": "mediacrawler"}}
