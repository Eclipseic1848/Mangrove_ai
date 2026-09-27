"""独立1.0.0候选复用原Worker契约，不替换现用版本或绕过宿主身份门。"""
import os
import unittest
from tests.test_coremind_worker_contract import CoreMindWorkerContractTests as _Base


@unittest.skipUnless(os.environ.get("MANGROVE_COREMIND_CANDIDATE_TEST") == "1", "仅在独立候选环境验证")
class CoreMindCandidateContractTests(_Base):
    expected_version = "1.0.0"


# 避免unittest把导入的基类当成第二套候选测试执行。
del _Base
