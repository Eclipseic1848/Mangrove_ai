#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""采集网络加固工具单元测试（限速/轮换/退避）。

运行：python scripts/test_collectors_net.py
"""
import asyncio
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors import _net
from src.config import settings


def test_pick_headers():
    h = _net.pick_headers()
    assert "User-Agent" in h and "Accept-Language" in h and "Accept" in h
    # 关闭轮换 → 固定首项（可复现）
    old = settings.collector_rotate_ua
    try:
        settings.collector_rotate_ua = False
        assert _net.pick_headers()["User-Agent"] == _net._UA_POOL[0]
    finally:
        settings.collector_rotate_ua = old
    # extra 合并
    assert _net.pick_headers({"Referer": "https://x"})["Referer"] == "https://x"


def test_rate_limiter():
    async def run():
        lim = _net.AsyncRateLimiter(min_interval=0.1)
        # 同域名两次 acquire：第二次需等待 >= ~0.1s
        t0 = time.monotonic()
        await lim.acquire("https://a.com/1")
        await lim.acquire("https://a.com/2")
        same = time.monotonic() - t0
        assert same >= 0.09, f"同域名应被限速，实际间隔 {same:.3f}s"
        # 不同域名：不应额外等待
        t1 = time.monotonic()
        await lim.acquire("https://b.com/1")
        other = time.monotonic() - t1
        assert other < 0.05, f"不同域名不应限速，实际 {other:.3f}s"

    asyncio.run(run())


def test_rate_limiter_random_range():
    """区间随机：多次同域名间隔应落在 [min,max] 内且不全相等（反固定节律）。"""
    async def run():
        lim = _net.AsyncRateLimiter(min_interval=0.05, max_interval=0.2)
        waits = []
        await lim.acquire("https://c.com/0")
        for i in range(1, 6):
            t0 = time.monotonic()
            await lim.acquire(f"https://c.com/{i}")
            waits.append(time.monotonic() - t0)
        assert all(w <= 0.25 for w in waits), f"间隔应不超过上限，实际 {waits}"
        assert max(waits) - min(waits) > 0.01, f"间隔应随机变化而非固定，实际 {waits}"

    asyncio.run(run())


def test_retry_async():
    async def run():
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("temporary")
            return "ok"

        # base=0 避免真实 sleep；retries=2 → 第3次成功
        res = await _net.retry_async(flaky, retries=2, base=0, jitter=False)
        assert res == "ok" and calls["n"] == 3, calls

        # 始终失败 → 超过重试次数后抛出
        calls2 = {"n": 0}

        async def always_fail():
            calls2["n"] += 1
            raise ValueError("boom")

        try:
            await _net.retry_async(always_fail, retries=1, base=0, jitter=False)
        except ValueError:
            pass
        else:
            raise AssertionError("应在重试耗尽后抛出原异常")
        assert calls2["n"] == 2, f"应尝试 2 次（1+1重试），实际 {calls2['n']}"

    asyncio.run(run())


def main():
    tests = [test_pick_headers, test_rate_limiter, test_rate_limiter_random_range, test_retry_async]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50)
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
