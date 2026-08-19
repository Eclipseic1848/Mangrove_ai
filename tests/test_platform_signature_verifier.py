# -*- coding: utf-8 -*-
"""#15 AC07-10 阶段 5 缺陷回归：平台装载门签名验证必须绑定主布局主体内容。

#13 的 OciPlatformSignatureVerifier 只对 signed/<run_id> 副本做 verify_local，
篡改主布局（实际物化来源）的 subject blob 时签名验证仍通过，自动隔离不触发
（靠物化阶段 ORAS digest 校验兜底）。本测试要求：verify 必须先校验主布局中
subject digest 对应的 blob 内容哈希，篡改即拒绝。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.capability_governance.models import (
    CapabilityGovernanceEvent,
    CapabilityGovernanceTarget,
    CapabilityLifecycle,
    CapabilityMaturity,
)
from src.capability_governance.runtime_gate import (
    OciPlatformSignatureVerifier,
)
from src.conversation_steering import CapabilityPack, ProcedureScope


def _digest_of(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _platform_pack(digest: str) -> CapabilityPack:
    return CapabilityPack(
        pack_id="gray-python-table",
        version="3.0.0",
        digest=digest,
        scope=ProcedureScope.PLATFORM,
        maturity=CapabilityMaturity.VERIFIED,
        owner_id=None,
    )


def _publication(target: CapabilityGovernanceTarget, digest: str) -> CapabilityGovernanceEvent:
    from src.capability_governance.models import CapabilityEligibility

    return CapabilityGovernanceEvent(
        event_type="platform_published",
        idempotency_key="test-publish",
        actor_id="actor-a",
        actor_role="admin",
        reason="测试发布",
        target=target,
        maturity=CapabilityMaturity.VERIFIED,
        lifecycle=CapabilityLifecycle.ACTIVE,
        eligibility=CapabilityEligibility.ELIGIBLE,
        platform_digest=digest,
        source_digest="sha256:" + "a" * 64,
        platform_validation_run_id="pfval_test",
        signing_signature_digest="sha256:" + "b" * 64,
        signing_public_key_sha256="c" * 64,
        audience="admin_gray",
    )


class _FakeSigningRuntime:
    """记录 verify_local 调用；默认返回通过结果。"""

    def __init__(self) -> None:
        self.calls = []

    def verify_local(self, request):
        self.calls.append(request)
        from src.capability_governance.oci_signing import SigningStepResult

        return SigningStepResult(
            subject_digest=request.subject_digest,
            signature_digest="sha256:" + "b" * 64,
            public_key_sha256="c" * 64,
            referrer_digests=("sha256:" + "d" * 64,),
        )


def _write_main_layout_blob(layout: Path, digest: str, content: bytes) -> None:
    blob = layout / "blobs/sha256" / digest.removeprefix("sha256:")
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(content)


class TestOciPlatformSignatureVerifierMainLayoutBinding:
    def test_verify_ok_when_main_layout_blob_matches(self, tmp_path) -> None:
        """主布局 subject blob 哈希与 digest 一致时，verify 通过。"""
        digest = _digest_of(b"manifest-content")
        layout = tmp_path / "platform"
        _write_main_layout_blob(layout, digest, b"manifest-content")
        runtime = _FakeSigningRuntime()
        verifier = OciPlatformSignatureVerifier(
            signing_runtime=runtime,
            platform_layout=layout,
            public_key_path=tmp_path / "key.pub",
        )
        target = CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id="gray-python-table",
            version="3.0.0",
            digest=digest,
        )
        result = verifier.verify(_platform_pack(digest), _publication(target, digest))
        assert result.subject_digest == digest

    def test_verify_rejects_when_main_layout_blob_tampered(self, tmp_path) -> None:
        """#15 缺陷回归：主布局 subject blob 被篡改（内容与 digest 失配）必须拒绝。"""
        digest = _digest_of(b"manifest-content")
        layout = tmp_path / "platform"
        # 篡改：写入与 digest 不一致的内容
        _write_main_layout_blob(layout, digest, b"manifest-tampered!")
        runtime = _FakeSigningRuntime()
        verifier = OciPlatformSignatureVerifier(
            signing_runtime=runtime,
            platform_layout=layout,
            public_key_path=tmp_path / "key.pub",
        )
        target = CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id="gray-python-table",
            version="3.0.0",
            digest=digest,
        )
        with pytest.raises(RuntimeError):
            verifier.verify(_platform_pack(digest), _publication(target, digest))
        # 签名运行时不应被调用（主布局校验先行，fail-closed）。
        assert runtime.calls == []

    def test_verify_rejects_when_main_layout_blob_missing(self, tmp_path) -> None:
        """主布局缺少 subject blob 必须失败关闭（不能静默放行）。"""
        digest = _digest_of(b"manifest-content")
        layout = tmp_path / "platform"
        runtime = _FakeSigningRuntime()
        verifier = OciPlatformSignatureVerifier(
            signing_runtime=runtime,
            platform_layout=layout,
            public_key_path=tmp_path / "key.pub",
        )
        target = CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id="gray-python-table",
            version="3.0.0",
            digest=digest,
        )
        with pytest.raises(RuntimeError):
            verifier.verify(_platform_pack(digest), _publication(target, digest))
        assert runtime.calls == []
