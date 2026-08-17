#!/usr/bin/env python3
"""
adapters/tsec.py -- 腾讯 Tsecbench 平台适配器 (实测 2026-08-15)。

接入协议 (平台接入文档):
  认证: 请求头 BENCHMARK_TOKEN: <UUID> (跑分任务 token，非网站登录 token)
  预检: GET http://10.0.100.58 -> {"status":"ok"} 表示 VPN 已连通 (强制前置)
  端点 (BASE_URL = https://tsecbench.zc.tencent.com):
    GET  /openapi/v1/challenges                      题目列表+进度
    POST /openapi/v1/challenges/start?unique_code=   启动靶机 -> container_addr[]
    GET  /openapi/v1/challenges/hint?unique_code=    提示 (扣分，不主动用)
    POST /openapi/v1/challenges/submit               {"unique_code","flag"}
    POST /openapi/v1/challenges/close?unique_code=   释放容器
  约束:
    - 活跃容器上限 3 (start 409 "max active" -> 先 close 再试)
    - 一题可多 flag (flag_count>1)，重复提交已正确 flag 返回 409 duplicate (幂等)
    - 靶机地址是 VPN 内网直连 (10.x)，请求必须直连不走代理
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import Challenge, PlatformAdapter, SubmitResult

VPN_CHECK_URL = "http://10.0.100.58"


class TSecError(Exception):
    """平台错误 (携带 HTTP 状态码)。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class TSecAdapter(PlatformAdapter):
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        # 题目前缀排除表 (逗号分隔，如 "b,f2"): 测试时跳过不想碰的题系
        self.exclude_prefixes = {
            p.strip() for p in os.environ.get("TSEC_EXCLUDE_PREFIXES", "").split(",") if p.strip()
        }
        # 直连不走系统代理 (平台与靶机都在 VPN/直连网络)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.vpn_ok, self.vpn_msg = self.check_vpn()

    # ─── 底层 ───

    def _request(self, method: str, path: str, body: dict | None = None,
                 timeout: int = 20) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("BENCHMARK_TOKEN", self.token)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            raise TSecError(e.code, f"HTTP {e.code}: {detail}") from None
        if not raw:
            return {}
        return json.loads(raw)

    def check_vpn(self) -> tuple[bool, str]:
        """VPN 联通预检 (强制前置)。"""
        try:
            with self._opener.open(VPN_CHECK_URL, timeout=6) as resp:
                data = json.loads(resp.read())
            if data.get("status") == "ok":
                return True, f"VPN ok (client_ip={data.get('client_ip')})"
            return False, f"预检点响应异常: {data}"
        except Exception as e:
            return False, f"VPN 预检失败: {e}"

    # ─── PlatformAdapter ───

    def list_challenges(self) -> list[Challenge]:
        items = self._request("GET", "/openapi/v1/challenges")
        out = []
        for x in items:
            if x.get("is_completed"):
                continue  # 已通关的跳过
            prefix = str(x["unique_code"]).split("-")[0]
            if prefix in self.exclude_prefixes:
                continue  # 用户显式排除的题系 (TSEC_EXCLUDE_PREFIXES)
            out.append(self._to_challenge(x))
        return out

    # 题目前缀 -> solver 题型 (实测 63 题交叉验证: a=web挖掘, b=多阶段渗透(网络靶机),
    # c=面板渗透, d=云, e1/e2/e3=对抗规避 —— 均按 web 打; f1=TCP内存安全, f2=MCU固件 —— 按 binary 打)
    _TYPE_BY_PREFIX = {
        "a": "web", "b": "web", "c": "web", "d": "web",
        "e1": "web", "e2": "web", "e3": "web",
        "f1": "binary", "f2": "binary",
    }

    @staticmethod
    def _to_challenge(x: dict) -> Challenge:
        running = x.get("container_status") == "available"
        addrs = x.get("container_addr") or []
        # 难度映射成 solve_count 参与"容易优先"排序 (easy > medium > hard);
        # 多 flag 题 (b 系列 killchain) 排序降权: 单 flag 回收快，先拿确定的分
        difficulty_rank = {"easy": 300, "medium": 200, "hard": 100}
        flag_count = int(x.get("flag_count") or 1)
        priority_rank = difficulty_rank.get(str(x.get("difficulty") or ""), 100)
        if flag_count > 1:
            priority_rank = priority_rank // 4   # 4/6-flag 多阶段题显著靠后
        cid = str(x["unique_code"])
        prefix = cid.split("-")[0]
        return Challenge(
            id=cid,
            title=f"{cid} · {(x.get('description') or '')[:40]}",
            type=TSecAdapter._TYPE_BY_PREFIX.get(prefix, "web"),
            score=int(x.get("total_score") or 0),
            solve_count=priority_rank,
            description=str(x.get("description") or ""),
            url=(f"http://{addrs[0]}" if running and addrs else None),
            flag_count=flag_count,
        )

    def start_challenge(self, cid: str) -> str:
        """
        启动靶机。409 max-active 时退避重试 —— close 到平台释放名额有传播延迟，
        立即重试会连续撞 409 (实测 2026-08-17 a-14/a-17)。
        """
        q = urllib.parse.quote(cid)
        last_err = ""
        for wait in (0, 4, 8):
            if wait:
                time.sleep(wait)
            try:
                data = self._request("POST", f"/openapi/v1/challenges/start?unique_code={q}")
            except TSecError as e:
                last_err = e.message
                if e.status == 409 and "max active" in e.message.lower():
                    continue  # 等平台释放名额后重试
                # 其他 409 (题已在跑等 invalid_state): 查列表复用现有地址
                if e.status == 409:
                    for x in self._request("GET", "/openapi/v1/challenges"):
                        if x.get("unique_code") == cid:
                            addrs = x.get("container_addr") or []
                            if x.get("container_status") == "available" and addrs:
                                return f"http://{addrs[0]}"
                raise
            addrs = data.get("container_addr") or []
            if not addrs:
                raise RuntimeError(f"start 未返回 container_addr: {data}")
            return f"http://{addrs[0]}"
        raise RuntimeError(f"start 409 且容器不可用: {last_err}")

    def stop_challenge(self, cid: str) -> None:
        q = urllib.parse.quote(cid)
        self._request("POST", f"/openapi/v1/challenges/close?unique_code={q}")

    def submit(self, cid: str, flag: str) -> SubmitResult:
        try:
            data = self._request(
                "POST", "/openapi/v1/challenges/submit",
                {"unique_code": cid, "flag": flag},
            )
        except TSecError as e:
            # 重复提交已正确的 flag: 平台幂等保护，视为正确 (已计分)
            if "duplicate" in e.message.lower():
                return SubmitResult(
                    "correct", "duplicate: 该 flag 已计过分",
                    data={"duplicate": True},
                )
            raise
        if data.get("correct"):
            return SubmitResult(
                "correct",
                f"+{data.get('awarded')} 分 (累计 {data.get('cumulative_score')})",
                data={
                    "correct_flag_count": int(data.get("correct_flag_count") or 0),
                    "total_flag_count": int(data.get("total_flag_count") or 1),
                },
            )
        return SubmitResult("wrong", json.dumps(data, ensure_ascii=False)[:200])

    def get_hint(self, cid: str) -> str:
        q = urllib.parse.quote(cid)
        data = self._request("GET", f"/openapi/v1/challenges/hint?unique_code={q}")
        return str(data.get("hint") or "")

    def duplicate_submit(self, cid: str, flag: str) -> bool:
        """409 duplicate 判定 (幂等: 该 flag 已计过分，无需重试)。"""
        try:
            self._request(
                "POST", "/openapi/v1/challenges/submit",
                {"unique_code": cid, "flag": flag},
            )
            return False
        except TSecError as e:
            return "duplicate" in e.message.lower()
