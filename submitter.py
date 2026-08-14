#!/usr/bin/env python3
"""
submitter.py -- flag 自动提交器 (master-agent-spec.md §4.6)。

单线程队列串行提交:
  - 频控: 相邻两次实际提交间隔 >= min_interval
  - 单题上限: submit_count < max_per_challenge (以 MasterState 为准， consume 时复查)
  - 平台错误 (网络/5xx 异常): 指数退避重试 10s/30s/60s，共 3 次
  - 结果通过 results() 队列交还 Master 主循环处理 (correct 时 Master 才回收槽位)
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Optional

from challenge_state import MasterState

log = logging.getLogger("master")

_BACKOFF_SECONDS = (10, 30, 60)
_SENTINEL = None  # type: ignore[assignment]


class Submitter:
    """后台提交线程。submit() 入队，results() 取结果。"""

    def __init__(
        self,
        adapter,
        state: MasterState,
        min_interval: float = 10.0,
    ):
        self.adapter = adapter
        self.state = state
        self.min_interval = min_interval
        self._q: "queue.Queue[Optional[tuple[str, str]]]" = queue.Queue()
        self._results: "queue.Queue[dict]" = queue.Queue()
        self._last_submit_at = 0.0
        self._stopped = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ─── 对外接口 ───

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="submitter")
            self._thread.start()

    def submit(self, cid: str, flag: str) -> None:
        self._q.put((cid, flag))

    def results(self) -> list[dict]:
        """非阻塞取走全部待处理结果。"""
        out = []
        try:
            while True:
                out.append(self._results.get_nowait())
        except queue.Empty:
            pass
        return out

    def has_pending(self) -> bool:
        return self._q.qsize() > 0

    def stop(self) -> None:
        self._stopped.set()
        self._q.put(_SENTINEL)
        if self._thread is not None:
            self._thread.join(timeout=3)

    # ─── 内部 ───

    def _run(self) -> None:
        while not self._stopped.is_set():
            item = self._q.get()
            if item is _SENTINEL:
                break
            cid, flag = item
            try:
                self._process(cid, flag)
            except Exception as e:  # 不让单条故障杀死提交线程
                log.error("[submitter] 处理 %s 提交异常: %s", cid, e)
                self._emit(cid, flag, "error", f"submitter 内部异常: {e}")

    def _process(self, cid: str, flag: str) -> None:
        # 单题上限复查 (Master 入队时也查过，这里是权威判定)
        if not self.state.can_submit(cid):
            self._emit(cid, flag, "skipped", "达到单题提交上限")
            return

        # 频控
        wait = self._last_submit_at + self.min_interval - time.time()
        if wait > 0 and self._stopped.wait(wait):
            self._emit(cid, flag, "error", "提交器停止，未提交")
            return

        # 占用一次提交配额 (平台错误重试不重复占用)
        self.state.record_submit(cid, flag, "submitting")

        # 平台错误退避重试
        result = None
        last_err = ""
        for backoff in (0,) + _BACKOFF_SECONDS:
            if backoff and self._stopped.wait(backoff):
                break
            self._last_submit_at = time.time()
            try:
                result = self.adapter.submit(cid, flag)
                break
            except Exception as e:
                last_err = str(e)
                result = None
                log.warning("[submitter] %s 提交失败 (%s)，退避 %ss 重试", cid, e, backoff or 0)

        if result is None:
            self._emit(cid, flag, "error", f"平台提交失败: {last_err}")
            return
        self._emit(cid, flag, result.status, result.message)

    def _emit(self, cid: str, flag: str, status: str, message: str = "") -> None:
        log.info("[submitter] %s flag=%s -> %s %s", cid, flag, status, message)
        self._results.put({"cid": cid, "flag": flag, "status": status, "message": message})
