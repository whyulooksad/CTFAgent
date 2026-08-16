#!/usr/bin/env python3
"""
adapters/live.py -- 真实赛方平台适配器 (面板「平台接入」手动输入 API 地址/Token)。

参考第二届腾讯智能渗透测试黑客松的接口形态实现 (best-effort):
  GET  /challenges       题目列表
  POST /start_challenge  开靶机
  POST /stop_challenge   释放靶机
  POST /submit           提交 flag
  GET  /hint             获取提示 (暂不主动调用)

字段映射做了常见命名兼容 (id/challenge_id、title/name、score/value 等)。
测试日拿到官方文档后只需核对本文件的路径与字段名 (master-agent-spec.md Phase 4)。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .base import Challenge, PlatformAdapter, SubmitResult

# 平台题型归一: run.sh 只支持 web/crypto/misc，其余按 misc 走本地附件流程
_TYPE_MAP = {
    "web": "web", "crypto": "crypto", "crypt": "crypto", "misc": "misc",
    "pwn": "misc", "reverse": "misc", "re": "misc", "forensics": "misc",
}


class LiveAdapter(PlatformAdapter):
    def __init__(self, base_url: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token
        # 不走系统代理 (赛方 API 常在内网/直连)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # ─── 底层请求 ───

    def _request(self, method: str, path: str, body: dict | None = None, timeout: int = 15) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            # 认证头测试日按官方文档核对，先两个都带
            req.add_header("Authorization", f"Bearer {self.token}")
            req.add_header("X-Token", self.token)
        with self._opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
        if not raw:
            return {}
        return json.loads(raw)

    # ─── 题目 ───

    def list_challenges(self) -> list[Challenge]:
        data = self._request("GET", "/challenges")
        items = data if isinstance(data, list) else (
            data.get("data") or data.get("challenges") or data.get("items") or []
        )
        return [self._to_challenge(x) for x in items if isinstance(x, dict)]

    @staticmethod
    def _to_challenge(x: dict) -> Challenge:
        cid = str(x.get("id") or x.get("challenge_id") or x.get("name") or "")
        raw_type = str(x.get("type") or x.get("category") or "misc").lower()
        return Challenge(
            id=cid,
            title=str(x.get("title") or x.get("name") or cid),
            type=_TYPE_MAP.get(raw_type, "misc"),
            score=int(x.get("score") or x.get("value") or 0),
            solve_count=int(
                x.get("solve_count") or x.get("solved_count") or x.get("solves") or 0
            ),
            description=str(x.get("description") or x.get("hint") or ""),
            url=x.get("url") or x.get("instance_url"),
            attachment_url=x.get("attachment") or x.get("attachment_url") or x.get("file"),
        )

    # ─── 靶机 ───

    def start_challenge(self, cid: str) -> str:
        try:
            data = self._request("POST", "/start_challenge", {"challenge_id": cid, "id": cid})
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
            return ""  # Master 会冷却重试
        return str(data.get("url") or data.get("instance_url") or "")

    def stop_challenge(self, cid: str) -> None:
        try:
            self._request("POST", "/stop_challenge", {"challenge_id": cid, "id": cid})
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
            pass  # 释放失败仅告警，不阻塞

    # ─── 提交 ───

    def submit(self, cid: str, flag: str) -> SubmitResult:
        try:
            data = self._request(
                "POST", "/submit", {"challenge_id": cid, "id": cid, "flag": flag}
            )
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as e:
            return SubmitResult("error", f"平台请求失败: {e}")
        if self._is_correct(data):
            return SubmitResult("correct", json.dumps(data, ensure_ascii=False)[:200])
        return SubmitResult("wrong", json.dumps(data, ensure_ascii=False)[:200])

    @staticmethod
    def _is_correct(data: dict) -> bool:
        if data.get("correct") is True or data.get("success") is True:
            return True
        status = str(data.get("status") or data.get("result") or "").lower()
        if status in ("correct", "accepted", "success", "right", "true"):
            return True
        msg = str(data.get("message") or data.get("detail") or "")
        return bool(re.search(r"正确|成功|accepted|correct", msg, re.IGNORECASE))

    # ─── 提示 / 附件 ───

    def get_hint(self, cid: str) -> str:
        try:
            data = self._request("GET", f"/hint?challenge_id={urllib.parse.quote(cid)}")
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
            return ""
        return str(data.get("hint") or data.get("data") or "")

    def download_attachment(self, url: str, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = url.split("?")[0].rstrip("/").split("/")[-1] or "attachment.bin"
        dest = dest_dir / name
        req = urllib.request.Request(url)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with self._opener.open(req, timeout=60) as resp, open(dest, "wb") as f:
            f.write(resp.read())
        return dest
