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
    """按 #12 签名侧构造对齐（signed/<run_id> Layout、digest 引用）做装载重验。"""

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
    ) -> None:
        self._projection_for = projection_for
        self._platform_publication_for = platform_publication_for
        self._signature_verifier = signature_verifier

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

    def check_mount(
        self,
        actor: CatalogActor,
        pack: CapabilityPack,
    ) -> None:
        projection = self._projection_for(pack)
        if pack.scope is ProcedureScope.PERSONAL:
            if pack.owner_id != actor.owner_id:
                raise self._reject(pack, "个人能力不属于当前用户")
            if projection.maturity is not CapabilityMaturity.VERIFIED:
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
            raise self._reject(
                pack, f"平台签名重验失败：{type(error).__name__}"
            ) from error
        if result.subject_digest != pack.digest:
            raise self._reject(pack, "签名主体 digest 与平台 Pack 不一致")
        if result.signature_digest != publication.signing_signature_digest:
            raise self._reject(pack, "签名 digest 与发布事件证据不一致")
        if result.public_key_sha256 != publication.signing_public_key_sha256:
            raise self._reject(pack, "签名公钥与发布事件证据不一致")
