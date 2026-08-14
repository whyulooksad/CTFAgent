#!/usr/bin/env python3
"""
tests/test_master.py -- Master 端到端测试 (mock 平台 + Fake 后端，不依赖 codex/hermes)。

覆盖:
  1. 优先级排序单测 (3 道 mock 题期望序: easy-misc > mid-web > mid-crypto)
  2. e2e: 3 题正常解出并自动提交 / 错误 flag 低价值不重试 /
     超时高价值重试一次 / max_challenges 去重计数 / web 靶机释放

真实 codex 链路 (ProcessBackend + mock 题) 的验证请在 WSL 上手动跑:
  python3 master.py   (master_config.json: adapter=mock, backend=process)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from adapters.base import Challenge, SubmitResult
from adapters.mock import MOCK_FLAGS, MockAdapter
from challenge_state import FAILED, SUBMITTED_CORRECT, TIMEOUT, extract_flags
from master import Config, Master
from prioritizer import rule_order
from solver_pool import FakeBackend


def test_extract_flags() -> None:
    """真实冒烟跑出的三种噪音格式 + 基本格式。"""
    cases = {
        # 2026-08-14 冒烟实测: misc 题 codex 写法
        "- `flag{mock_easy_misc_welcome}`": "flag{mock_easy_misc_welcome}",
        # crypto 题 codex 写法
        "- flag{mock_xor_is_easy}": "flag{mock_xor_is_easy}",
        # web 题 codex 写法 (带来源注释)
        "- flag{mock_web_hidden}（来源：首页 HTML 注释）": "flag{mock_web_hidden}",
        # 干净格式
        "flag{abc_123}": "flag{abc_123}",
        "CTF{xyz}": "CTF{xyz}",
        # 非 flag{} 前缀: 回退清理后的原始行 (不猜格式)
        "- `SCTF{exotic_format}`": "SCTF{exotic_format}",
    }
    for raw, expected in cases.items():
        got = extract_flags(f"## Flags Found\n{raw}\n")
        assert got == [expected], f"{raw!r} -> {got} != [{expected!r}]"
    assert extract_flags("## Flags Found\n(无)\n") == []
    assert extract_flags("## Flags Found\n<!-- 进度笔记 -->\n") == []
    assert extract_flags("## Flags Found\nflag{a}\n\n## Next\nx") == ["flag{a}"]
    print("[PASS] extract_flags")


class TestAdapter(MockAdapter):
    """扩展 2 道测异常路径的题 (title 标记驱动 FakeBackend 行为)。"""

    def list_challenges(self) -> list[Challenge]:
        return super().list_challenges() + [
            Challenge(
                id="t-wrong",
                title="[wrong] 假flag题",
                type="misc",
                score=50,
                solve_count=100,
                description="会产出一个错误 flag，测错误提交路径",
            ),
            Challenge(
                id="t-fail-hard",
                title="[fail] 高分难题",
                type="misc",
                score=500,
                solve_count=2,
                description="永远解不出来，测超时与高价值重试",
            ),
        ]

    def submit(self, cid: str, flag: str) -> SubmitResult:
        if cid == "t-wrong":
            return SubmitResult("wrong", "test: 恒定错误")
        return super().submit(cid, flag)


def test_rule_order() -> None:
    order = [c.id for c in rule_order(MockAdapter().list_challenges())]
    expected = ["mock-easy-misc", "mock-mid-web", "mock-mid-crypto"]
    assert order == expected, f"优先级排序错误: {order} != {expected}"
    print(f"[PASS] rule_order: {order}")


def test_e2e() -> None:
    state_file = SCRIPT_DIR / "tests" / "master_state_test.json"
    log_file = SCRIPT_DIR / "tests" / "master_test.log"
    for p in (state_file, log_file):
        p.unlink(missing_ok=True)

    cfg = Config(
        adapter="mock",
        backend="fake",
        max_solvers=2,
        max_challenges=5,
        solver_timeout=6,
        poll_interval=0.5,
        submit_min_interval=0.2,
        state_file=str(state_file),
        log_file=str(log_file),
    )
    adapter = TestAdapter()
    backend = FakeBackend(flag_lookup=MOCK_FLAGS.get, solve_delay=0.5)
    m = Master(cfg, adapter=adapter, backend=backend)

    t0 = time.time()
    m.run()
    elapsed = time.time() - t0
    assert elapsed < 120, f"e2e 耗时异常: {elapsed:.1f}s"

    recs = {r.id: r for r in m.state.all_records()}
    assert set(recs) == {
        "mock-easy-misc", "mock-mid-crypto", "mock-mid-web", "t-wrong", "t-fail-hard",
    }, f"题目集合不符: {set(recs)}"

    # 3 道 mock 题全部解出且 flag 正确
    for cid, expected_flag in MOCK_FLAGS.items():
        r = recs[cid]
        assert r.status == SUBMITTED_CORRECT, f"{cid}: {r.status} != submitted_correct"
        assert r.flag == expected_flag, f"{cid}: {r.flag} != {expected_flag}"

    # 错误 flag: 低价值题 (value=0.1, rarity=0.5) 不重试
    r = recs["t-wrong"]
    assert r.status == FAILED and r.attempts == 1, \
        f"t-wrong: {r.status} attempts={r.attempts}"
    assert r.last_submit_status == "wrong", r.last_submit_status

    # 高价值难题 (value=1.0): 超时重试一次后终态
    r = recs["t-fail-hard"]
    assert r.status == TIMEOUT and r.attempts == 2, \
        f"t-fail-hard: {r.status} attempts={r.attempts}"

    # 题目数上限按去重计
    assert m.state.distinct_attempted() == 5

    # web 靶机已释放
    assert not adapter._servers, f"靶机泄漏: {adapter._servers}"

    assert not m.running
    print(f"[PASS] e2e ({elapsed:.1f}s)")


if __name__ == "__main__":
    test_extract_flags()
    test_rule_order()
    test_e2e()
    print("ALL PASS")
