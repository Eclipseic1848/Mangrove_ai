"""按来源读取图片原件与 OCR，供各主题抽取共用。"""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit, urlunsplit

import httpx

from src.config.settings import settings
from src.connectors.http_security import HttpSecurityGuard
from src.data_prep.artifact_store import ArtifactStore
from src.model_connections.pinned_transport import PinnedAsyncHTTPTransport
from src.parsers.image import ImageParser
from src.services.upload_store import inspect_uploaded_image
from src.api.execution import execution_checkpoint, execution_to_thread


async def read_images(note, task_id, raw_only):
    store = ArtifactStore()
    parser = ImageParser(artifact_store=store) if not raw_only else None
    results = []
    for index, url in enumerate(note["metadata"].get("image_urls") or [], 1):
        execution_checkpoint()
        row = {"image_index": index, "source_url": url, "status": "failed", "text": None,
               "elements": [], "image_type": None}
        results.append(row)
        try:
            parts = urlsplit(url)
            image_hosts = ("sns-webpic-qc.xhscdn.com",)
            if parts.hostname in image_hosts and parts.scheme == "http" and parts.port in (None, 80) and not parts.username and not parts.password:
                parts = parts._replace(scheme="https", netloc=parts.hostname)
            download_url = urlunsplit(parts)
            # 仅已核验 CDN 的标准 HTTPS 端口兼容本机代理虚拟 IP，私网与其他域名仍拒绝。
            guard = HttpSecurityGuard(proxy_fake_ip_host_allowlist=image_hosts if parts.port in (None, 443) else ())
            target = await asyncio.to_thread(guard.validate, download_url)
            transport = PinnedAsyncHTTPTransport(target=target, check_active=execution_checkpoint)
            async with httpx.AsyncClient(transport=transport, trust_env=False, timeout=30,
                                        follow_redirects=False) as client:
                async with client.stream("GET", download_url) as response:
                    response.raise_for_status()
                    media_type = response.headers.get("content-type", "").split(";")[0].lower()
                    if media_type not in ImageParser.media_types:
                        raise ValueError("不支持的图片类型")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        execution_checkpoint()
                        data.extend(chunk)
                        if len(data) > settings.data_prep_max_upload_bytes:
                            raise ValueError("图片超过平台上传限制")
            raw = bytes(data)
            row["image_size"] = inspect_uploaded_image(raw)
            artifact = store.write_raw(task_id, note["metadata"]["note_id"], raw,
                                       uri=url, media_type=media_type)
            row.update(sha256=artifact.sha256, artifact_path=artifact.storage_path,
                       media_type=media_type, status="downloaded", reason="raw_only" if raw_only else None)
            if parser is not None:
                records, rejects = await execution_to_thread(parser.parse, artifact, raw)
                if not records:
                    row.update(status="failed", reason="image_ocr_required")
                    continue
                elements = []
                for record in records:
                    for element in record.data.get("elements") or []:
                        elements.append({key: value for key, value in element.items() if key != "raw_result_ref"})
                row.update(status="recognized", text="\n".join(record.data.get("text", "") for record in records),
                           elements=elements, review_required=any(element.get("review_required") for element in elements))
        except Exception:
            # 不把含认证参数的网络错误或本机路径写进客户证据。
            row.update(status="failed", reason="image_read_or_parse_failed")
        execution_checkpoint()
    return results
