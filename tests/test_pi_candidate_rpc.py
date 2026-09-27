"""候选验证器不能因持续事件绕过超时或把清理失败报告为成功。"""
import queue
import subprocess
import time
from unittest.mock import patch

import pytest

from scripts.verify_pi_candidate_rpc import next_line, remove_container


def test_expired_deadline_rejects_even_when_events_remain():
    lines = queue.Queue()
    lines.put("event")
    with pytest.raises(queue.Empty):
        next_line(lines, time.monotonic() - 1)
    assert lines.qsize() == 1


@pytest.mark.parametrize("result,expected", [
    (subprocess.CompletedProcess([], 0, "", ""), True),
    (subprocess.CompletedProcess([], 0, "container-id", ""), False),
    (subprocess.CompletedProcess([], 1, "", "daemon unavailable"), False),
])
def test_cleanup_requires_confirmed_absence(result, expected):
    with patch("scripts.verify_pi_candidate_rpc.subprocess.run", return_value=result):
        assert remove_container("fixture") is expected


def test_cleanup_timeout_returns_failure():
    with patch("scripts.verify_pi_candidate_rpc.subprocess.run",
               side_effect=subprocess.TimeoutExpired("docker", 10)):
        assert remove_container("fixture") is False
