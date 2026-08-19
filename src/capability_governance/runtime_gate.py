# -*- coding: utf-8 -*-
"""运行时装载治理门：三轴、受众与平台签名在唯一装载 Seam 前失败关闭。"""
from __future__ import annotations

from typing import Callable, Protocol

from src.capability_catalog import CapabilityMountGateRejected
from src.capability_catalog.models import CatalogActor
from src.conversation_steering import CapabilityPack, ProcedureScope

from .models import (
    CapabilityEligibility,
    CapabilityGovernanceEvent,
    CapabilityGovernanceProjection,
    CapabilityGovernanceTarget,
    CapabilityLifecycle,
    CapabilityMaturity,
)
from .oci_signing import SigningStepResult


class PlatformSignatureVerifier(Protocol):
    """装载前对平台 Pack 做 OCI 签名重验；实现直用 #9 verify_local。"""

    def verify(
        self,
        pack: CapabilityPack,
        publication: CapabilityGovernanceEvent,
    ) -> SigningStepResult: ...


class OciPlatformSignatureVerifier:
    """按 #12 签名侧构造对齐（signed/<run_id> Layout、digest 引用）做装载重验。

    #15 AC07-10 阶段 5 修复：签名验证前必须校验主布局 subject blob 内容哈希。
    verify_local 只对 signed/<run_id> 独立副本重验签名；若主布局（实际物化
    来源）的 subject blob 被篡改，仅靠物化阶段 ORAS digest 校验兜底，自动
    隔离不会触发。这里在签名重验前先从主布局读取 subject blob 并比对内容
    哈希，失配即拒绝（fail-closed 并把篡改证据交给自动隔离钩子）。
    """

    def __init__(
        self,
        *,
        signing_runtime,
        platform_layout,
        public_key_path,
    ) -> None:
        self._signing_runtime = signing_runtime
        self._platform_layout = platform_layout
        self._public_key_path = public_key_path

    def verify(
        self,
        pack: CapabilityPack,
        publication: CapabilityGovernanceEvent,
    ) -> SigningStepResult:
        run_id = publication.platform_validation_run_id
        if run_id is None:
            raise RuntimeError("发布事件缺少平台验证运行标识")
        if (
            publication.platform_digest is None
            or publication.platform_digest != pack.digest
        ):
            raise RuntimeError("发布事件 platform_digest 与 Pack 不一致")
        # 主布局 subject blob 内容哈希必须与冻结 digest 一致（#15 阶段 5）。
        # 篡改主布局 blob -> 哈希失配 -> 拒绝，触发自动隔离（S1 钩子）。
        import hashlib

        from pathlib import Path

        subject_blob = (
            Path(self._platform_layout)
            / "blobs/sha256"
            / pack.digest.removeprefix("sha256:")
        )
        if not subject_blob.is_file():
            raise RuntimeError("平台主布局缺少主体 manifest blob")
        actual = "sha256:" + hashlib.sha256(
            subject_blob.read_bytes()
        ).hexdigest()
        if actual != pack.digest:
            raise RuntimeError("平台主布局主体 manifest blob 内容与 digest 不一致")
        from .oci_signing import OciSigningRequest

        request = OciSigningRequest(
            transaction_id=(
                "load-" + pack.digest.removeprefix("sha256:")[:12]
            ),
            source_layout=self._platform_layout,
            source_reference=pack.digest,
            output_layout=self._platform_layout / "signed" / run_id,
            output_reference=pack.digest,
            registry_repository="mangrove/platform-snapshots",
            subject_digest=pack.digest,
            public_key_path=self._public_key_path,
        )
        return self._signing_runtime.verify_local(request)


class CapabilityGovernanceRuntimeGate:
    """个人三轴门 + 平台受众/签名门；legacy 平台包维持旧路径直至 #17 切换。"""

    def __init__(
        self,
        *,
        projection_for: Callable[
            [CapabilityPack], CapabilityGovernanceProjection
        ],
        platform_publication_for: Callable[
            [CapabilityPack], CapabilityGovernanceEvent | None
        ],
        signature_verifier: PlatformSignatureVerifier | None = None,
        auto_quarantine: Callable[[CapabilityPack, str], None] | None = None,
    ) -> None:
        self._projection_for = projection_for
        self._platform_publication_for = platform_publication_for
        self._signature_verifier = signature_verifier
        # 自动隔离钩子（#15 AC07-10）：默认为 None 保持 #13 只读行为；
        # 装配层注入真实实现后，真实验签失败会自动写隔离事件。
        self._auto_quarantine = auto_quarantine

    def _reject(
        self,
        pack: CapabilityPack,
        reason: str,
    ) -> CapabilityMountGateRejected:
        return CapabilityMountGateRejected(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
            reason=reason,
        )

    def _trigger_auto_quarantine(self, pack: CapabilityPack, reason: str) -> None:
        if self._auto_quarantine is None:
            return
        try:
            self._auto_quarantine(pack, reason)
        except Exception:
            # 自动隔离失败不得改变门的拒绝契约：装载照常失败关闭，
            # 隔离留待下次触发机会或管理员人工命令补写。
            pass

    def check_mount(
        self,
        actor: CatalogActor,
        pack: CapabilityPack,
        *,
        validation_exempt: bool = False,
    ) -> None:
        projection = self._projection_for(pack)
        if pack.scope is ProcedureScope.PERSONAL:
            if pack.owner_id != actor.owner_id:
                raise self._reject(pack, "个人能力不属于当前用户")
            if (
                projection.maturity is not CapabilityMaturity.VERIFIED
                and not validation_exempt
            ):
                # #15 D9 验证任务豁免：仅验证目标（冻结 selection 携带标记、
                # 本人所有、active、eligible）放行 draft；其余条件仍强制。
                raise self._reject(pack, "成熟度未达到 verified")
            if projection.lifecycle not in {
                CapabilityLifecycle.ACTIVE,
                CapabilityLifecycle.DEPRECATED,
            }:
                # DEPRECATED 例外（AC3/Q4）：已冻结历史任务允许恢复装载；
                # 新任务由列表过滤与冻结拦截两层挡住。
                raise self._reject(pack, "生命周期不是 active 或 deprecated")
            if projection.eligibility is not CapabilityEligibility.ELIGIBLE:
                raise self._reject(pack, "运行资格不是 eligible")
            return
        # 平台 Pack：无发布事件的 legacy 兼容投影维持 AC-06 旧路径放行（Q2）。
        if projection.source == "legacy_compat":
            return
        if projection.maturity is not CapabilityMaturity.VERIFIED:
            raise self._reject(pack, "成熟度未达到 verified")
        if projection.lifecycle not in {
            CapabilityLifecycle.ACTIVE,
            CapabilityLifecycle.DEPRECATED,
        }:
            # DEPRECATED 例外与个人分支一致：历史恢复装载放行。
            raise self._reject(pack, "生命周期不是 active 或 deprecated")
        if projection.eligibility is not CapabilityEligibility.ELIGIBLE:
            raise self._reject(pack, "运行资格不是 eligible")
        if projection.audience is None:
            # 有治理事件却无受众是异常形态；防御性拒绝，不静默放行。
            raise self._reject(pack, "平台能力缺少受众投影")
        if projection.audience == "admin_gray" and not actor.is_admin:
            raise self._reject(pack, "平台能力受众为管理员灰度，当前用户不可装载")
        publication = self._platform_publication_for(pack)
        if publication is None:
            raise self._reject(pack, "平台能力缺少发布事件，无法验证签名")
        if self._signature_verifier is None:
            raise self._reject(pack, "平台签名验证器未配置")
        try:
            result = self._signature_verifier.verify(pack, publication)
        except Exception as error:
            reason = f"平台签名重验失败：{type(error).__name__}"
            # 真实验签失败是篡改/签名损坏证据，触发自动隔离（#15 AC07-10）。
            self._trigger_auto_quarantine(pack, reason)
            raise self._reject(pack, reason) from error
        if result.subject_digest != pack.digest:
            reason = "签名主体 digest 与平台 Pack 不一致"
            self._trigger_auto_quarantine(pack, reason)
            raise self._reject(pack, reason)
        if result.signature_digest != publication.signing_signature_digest:
            reason = "签名 digest 与发布事件证据不一致"
            self._trigger_auto_quarantine(pack, reason)
            raise self._reject(pack, reason)
        if result.public_key_sha256 != publication.signing_public_key_sha256:
            reason = "签名公钥与发布事件证据不一致"
            self._trigger_auto_quarantine(pack, reason)
            raise self._reject(pack, reason)
