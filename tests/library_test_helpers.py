"""自学习脚本共用隔离：临时双库与 fake 模型接缝。"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_library_services(monkeypatch, tmp_path):
    from src.config.settings import settings
    from src.memory import embeddings, templates, lessons
    monkeypatch.setattr(settings, "embedding_enabled", False)
    monkeypatch.setattr(templates, "TEMPLATES_DIR", tmp_path / "templates")
    monkeypatch.setattr(lessons, "LESSONS_DIR", tmp_path / "lessons")
    monkeypatch.setattr("src.api.auth.get_store", lambda: None)
    monkeypatch.setattr(embeddings, "embed_texts_with_model", lambda texts: None)
    monkeypatch.setattr(embeddings, "is_rerank_configured", lambda: False)
    monkeypatch.setattr(embeddings, "rerank_scores", lambda *args, **kwargs: None)
    async def unavailable(*args, **kwargs):
        raise RuntimeError("测试禁止真实模型")
    monkeypatch.setattr(templates, "achat", unavailable)
    monkeypatch.setattr(lessons, "achat", unavailable)
