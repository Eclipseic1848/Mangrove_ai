"""知识库页面只返回当前 Owner 可用内容，不公开私有来源链。"""
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.memory._library_scope import content_digest
from .auth import is_admin_role


class LibraryShareIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    keywords: list[Annotated[str, StringConstraints(min_length=1, max_length=200)]] = Field(max_length=50)
    body: str = Field(min_length=1, max_length=100_000)
    data_type: str = Field(max_length=40)
    expected_source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]


def library_entry(entry: dict, user: dict) -> dict:
    fields = ("slug", "title", "keywords", "body", "data_type", "status", "uses", "quality_avg", "occurrences", "helped_avoid", "created_at", "scope", "content_digest")
    result = {key: entry[key] for key in fields if key in entry}
    own = entry.get("owner_id") == user["user_id"]
    result["is_owner"] = own
    result["can_delete"] = own or (entry.get("scope") == "platform" and is_admin_role(user.get("role")))
    result["content_digest"] = content_digest(entry)
    return result


def share_entry(share, slug: str, body: LibraryShareIn, user: dict) -> dict:
    fields = body.model_dump()
    # Owner 确认的是请求中的完整副本，摘要只绑定这份确切内容，不代表自动脱敏证明。
    try:
        entry = share(slug, owner_id=user["user_id"], expected_content_digest=content_digest(fields), **fields)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail="条目不存在或不可分享") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="条目已变化或未满足分享条件，请重新预览") from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="分享结果未确认，请刷新后核对") from exc
    return {"entry": library_entry(entry, user)}
