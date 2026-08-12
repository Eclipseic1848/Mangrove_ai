"""显式视频链接的证据提取：字幕、转写与画面文字彼此独立并保留来源。"""
from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx

from src.config.settings import settings

logger = logging.getLogger(__name__)

_VTT_TIMING_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s+-->")
_VTT_TAG_RE = re.compile(r"<[^>]+>")


def _subtitle_text(work_dir: Path) -> str:
    """读取 yt-dlp 下载的字幕，并去除时间戳与简单标记。"""
    parts: List[str] = []
    for path in work_dir.glob("*.vtt"):
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line == "WEBVTT" or _VTT_TIMING_RE.match(line):
                    continue
                line = _VTT_TAG_RE.sub("", line)
                if line and (not parts or parts[-1] != line):
                    parts.append(line)
        except OSError:
            logger.warning("读取字幕失败：%s", path, exc_info=True)
    return "\n".join(parts).strip()


def _download_with_ytdlp(url: str, work_dir: Path, cookie: str = '') -> Tuple[Path | None, str]:
    """下载单个视频及可获得的字幕；依赖缺失或下载失败时返回原因。"""
    try:
        import yt_dlp
    except ImportError:
        return None, "未安装 yt-dlp"

    options = {
        "format": "best[height<=480]/worst",
        "outtmpl": str(work_dir / "video.%(ext)s"),
        "noplaylist": True,
        "proxy": "",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["zh-Hans", "zh", "en"],
        "max_filesize": settings.video_max_download_mb * 1024 * 1024,
    }
    if cookie:
        options["http_headers"] = {"Cookie": cookie}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = Path(ydl.prepare_filename(info))
    except Exception as exc:
        return None, f"视频下载失败：{type(exc).__name__}"
    if not filename.exists():
        candidates = [p for p in work_dir.iterdir() if p.suffix.lower() not in {".vtt", ".srt"}]
        filename = candidates[0] if candidates else filename
    if not filename.exists():
        return None, "视频下载后未找到媒体文件"
    return filename, ""


async def _remote_asr(video_path: Path) -> str:
    """调用可选的 OpenAI 兼容语音识别服务。"""
    if not settings.video_asr_base_url:
        return ""
    endpoint = settings.video_asr_base_url.rstrip("/") + "/audio/transcriptions"
    headers = {"Authorization": f"Bearer {settings.video_asr_api_key}"} if settings.video_asr_api_key else {}
    try:
        with video_path.open("rb") as media:
            files = {"file": (video_path.name, media, "application/octet-stream")}
            data = {"model": settings.video_asr_model}
            async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
                response = await client.post(endpoint, headers=headers, data=data, files=files)
                response.raise_for_status()
        payload = response.json()
        return str(payload.get("text") or "").strip()
    except Exception:
        logger.warning("远程语音转写失败", exc_info=True)
        return ""


def _local_asr(video_path: Path) -> str:
    """在显式配置模型时尝试本地 faster-whisper。"""
    if not settings.video_asr_local_model:
        return ""
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(settings.video_asr_local_model)
        segments, _ = model.transcribe(str(video_path))
        return "".join(segment.text for segment in segments).strip()
    except Exception:
        logger.warning("本地语音转写失败", exc_info=True)
        return ""


def _visual_text(video_path: Path) -> str:
    """复用已有 Qwen 视频能力提取画面文字与讲述结构。"""
    try:
        from src.services.qwen_video_processor.processor import analyze_video_file
        result = analyze_video_file(
            str(video_path),
            question=(
                "请提取视频画面中可见的标题、字幕、界面文字和关键演示步骤。"
                "只陈述能从画面确认的信息，不要补充无法确认的事实。"
            ),
            slice_seconds=settings.video_slice_seconds,
        )
        return str(result.get("summary") or "").strip() if result.get("success") else ""
    except Exception:
        logger.warning("视频画面文字提取失败", exc_info=True)
        return ""


async def extract_video_evidence(item: Dict[str, Any]) -> Dict[str, Any]:
    """提取一个已验明身份的视频目标的证据包，不把简介当作视频内容证据。"""
    metadata = item.get("metadata") or {}
    source_url = metadata.get("media_url") or metadata.get("canonical_url") or item.get("url") or ""
    evidence: List[Dict[str, str]] = []
    if not source_url:
        return {"ready": False, "reason": "目标缺少可访问的视频地址", "sources": evidence}

    with tempfile.TemporaryDirectory(prefix="mangrove_video_") as temp_dir:
        work_dir = Path(temp_dir)
        cookie = settings.mc_cookie_dy if metadata.get("platform") == "dy" else ""
        video_path, error = await asyncio.to_thread(_download_with_ytdlp, source_url, work_dir, cookie)
        subtitles = _subtitle_text(work_dir)
        if subtitles:
            evidence.append({"type": "字幕", "text": subtitles})
        if video_path is None:
            return {"ready": False, "reason": error, "sources": evidence}

        transcript = await _remote_asr(video_path)
        if not transcript:
            transcript = await asyncio.to_thread(_local_asr, video_path)
        if transcript:
            evidence.append({"type": "语音转写", "text": transcript})

        visual = await asyncio.to_thread(_visual_text, video_path)
        if visual:
            evidence.append({"type": "画面文字", "text": visual})

    total_chars = sum(len(source["text"]) for source in evidence)
    if total_chars < settings.video_min_evidence_chars:
        return {
            "ready": False,
            "reason": f"可验证视频证据不足（{total_chars}/{settings.video_min_evidence_chars} 字）",
            "sources": evidence,
        }
    return {"ready": True, "reason": "", "sources": evidence}
