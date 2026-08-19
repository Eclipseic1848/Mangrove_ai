# -*- coding: utf-8 -*-
"""#15 AC07-10 阶段 3 复验：两个已发布平台快照的独立 Layout 密码学重验。

对每个签名事务输出 Layout 执行 verify_local（回环 Registry + ORAS 递归复制 +
Cosign 验签），核对：
  1. 主体 digest 与运行记录一致；
  2. 签名 Referrer digest 与运行记录一致；
  3. 公钥 SHA-256 与运行记录一致（同一平台签名身份）；
  4. 复验结束后临时 Registry 零残留。

用法：
  python scripts/ac07_10_verify_platform_signatures.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.api.capability_governance_runtime import get_platform_signing_runtime
from src.capability_governance.oci_signing import OciSigningRequest
from src.config.settings import settings

# (run_id, 主体 digest, 签名 digest, 公钥 SHA-256) 从生产库核验读取。
EXPECTED = {
    "2.0.0": {
        "run_id": "pfval_5710df08f02d4ec9ab0d",
        "subject": "sha256:5326dfae94da035f611260935bbc4afa8de1e9106c4f74a11fac5f642e259246",
        "signature": "sha256:b83e274c3b9ca90472a315af71805a86c4905189fe943ba9741cc79b38d52bf8",
        "pubkey": "103de227b8f540cb422b2bc53b2896849f9ccfcee446a686de0dd3ab02a9f79e",
    },
    "3.0.0": {
        "run_id": "pfval_6c38548b38404b29b955",
        "subject": "sha256:b462e5775614cd2a75e59eda5ac774b864485ffaacbcba6e957463e76f790944",
        "signature": "sha256:529f70ac7080ae8ab7fecbf89416ac1856279bec98a0b61554399913b011a5c7",
        "pubkey": "103de227b8f540cb422b2bc53b2896849f9ccfcee446a686de0dd3ab02a9f79e",
    },
}

# 重建后（方案 A，修复缺 purpose）的新签名事务，单独复验。
REBUILT = {
    "2.0.0": {
        "run_id": "pfval_975b628b7d644c2e8036",
        "subject": "sha256:e5556f83e889a62fc0ce9d3f856db89c07fcc56dddc0d2ee2582b89cf2931bfb",
        "signature": "sha256:e35c50d83c6b2592d35cfe23ad2581478a9713dac049d8473e5cc7977d654aff",
        "pubkey": "103de227b8f540cb422b2bc53b2896849f9ccfcee446a686de0dd3ab02a9f79e",
    },
    "3.0.0": {
        "run_id": "pfval_2d816c74238b45b0bc8d",
        "subject": "sha256:9379fe2908a4f8c1827fbe1db94d66892dc62190ec3da67129a64ae0ef0dbe03",
        "signature": "sha256:846394765c3dcbb6a5c71ffc05a5d00ea2ca6bd93f1ed106077815a5e601ddd9",
        "pubkey": "103de227b8f540cb422b2bc53b2896849f9ccfcee446a686de0dd3ab02a9f79e",
    },
}


def main() -> int:
    runtime = get_platform_signing_runtime()
    layout_root = (
        Path(settings.capability_platform_oci_layout_path) / "signed"
    )
    ok = True
    # 复验重建后（方案 A）的两个新签名事务。
    for version, spec in REBUILT.items():
        output_layout = layout_root / spec["run_id"]
        request = OciSigningRequest(
            transaction_id=f"verify-ac07-10-{version}",
            source_layout=Path(settings.capability_platform_oci_layout_path),
            source_reference=spec["subject"],
            output_layout=output_layout,
            output_reference=spec["subject"],
            registry_repository="mangrove/platform-snapshots",
            subject_digest=spec["subject"],
            public_key_path=Path(
                settings.capability_platform_signing_public_key
            ),
        )
        try:
            verified = runtime.verify_local(request)
        except Exception as error:
            print(f"[FAIL] v{version} 复验异常: {error}")
            ok = False
            continue
        checks = {
            "subject_digest": verified.subject_digest == spec["subject"],
            "signature_digest": verified.signature_digest == spec["signature"],
            "public_key": verified.public_key_sha256 == spec["pubkey"],
        }
        status = "PASS" if all(checks.values()) else "FAIL"
        if status == "FAIL":
            ok = False
        print(
            f"[{status}] v{version} 独立 Layout 复验 "
            f"subject={verified.subject_digest[:16]}… "
            f"sign={verified.signature_digest[:16]}… "
            f"pubkey={verified.public_key_sha256[:12]}… "
            f"checks={checks}"
        )
    print("ALL VERIFY PASS" if ok else "VERIFY FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    raise SystemExit(main())
