#!/usr/bin/env python3
"""
adapters/dasctf.py -- DASCTF 比赛平台 Agent API 适配器。

协议 (docs/api_doc.md):
  Base URL: {serverHost}/slab-match/api/v1/agent
  认证:     Header `X-Agent-AccessKey: <AccessKey>`
  统一响应: {"code":"00000","message":"","data":...}  (code=="00000" 成功)

接口:
  GET  /ctf/exercise-list            题目列表 (分类 -> corpus)
  GET  /ctf/exercise?exerciseId=     题目详情 (附件/靶机/环境状态)
  POST /ctf/build-exercise-env       启动环境 (异步, 需轮询详情到 isNeedCheck=false)
  POST /ctf/recover-exercise-env     回收环境
  POST /answer-panel/answer          提交 flag -> {"isCorrect": true}
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from .base import Challenge, PlatformAdapter, SubmitResult

API_PREFIX = "/slab-match/api/v1/agent"

# 分类名 -> master 类型 (exercise-list 的 category.name)
_TYPE_MAP = {
    "web": "web", "web安全": "web",
    "crypto": "crypto", "密码": "crypto", "密码学": "crypto",
    "misc": "misc", "杂项": "misc",
    "re": "reverse", "reverse": "reverse", "逆向": "reverse",
    "pwn": "pwn", "二进制": "pwn",
    "forensics": "forensics", "取证": "forensics",
    "osint": "osint",
}

_ENV_POLL_TIMEOUT = 300   # build 后等待环境就绪的最长时间 (秒)
_ENV_POLL_INTERVAL = 5


class DasctfAdapter(PlatformAdapter):
    def __init__(self, base_url: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.access_key = token  # X-Agent-AccessKey
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # ─── 底层请求 ───

    def _request(self, method: str, path: str, body: dict | None = None,
                 timeout: int = 20) -> dict:
        url = self.base_url + API_PREFIX + path
        if body is not None and method == "GET":
            url += "?" + urllib.parse.urlencode(body)
            data = None
        else:
            data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Agent-AccessKey", self.access_key)
        # 平台 WAF 拦截 python 默认 UA (Python-urllib/3.12 -> 403), 必须伪装浏览器
        req.add_header("User-Agent",
                       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        with self._opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
        obj = json.loads(raw) if raw else {}
        if obj.get("code") not in (None, "00000", 0):
            raise RuntimeError(f"DASCTF API {path} 失败: code={obj.get('code')} msg={obj.get('message')}")
        return obj.get("data") if isinstance(obj, dict) else obj

    # ─── 题目 ───

    def list_challenges(self) -> list[Challenge]:
        cats = self._request("GET", "/ctf/exercise-list")
        out: list[Challenge] = []
        for cat in cats if isinstance(cats, list) else []:
            raw_type = str(cat.get("name") or "").lower()
            ctype = _TYPE_MAP.get(raw_type, "misc")
            for ex in cat.get("corpus") or []:
                if not ex.get("isOpen", False):
                    continue
                cid = str(ex.get("id") or "")
                if not cid:
                    continue
                out.append(Challenge(
                    id=cid,
                    title=str(ex.get("name") or cid),
                    type=ctype,
                    score=0,          # 详情接口才有 score, 列表阶段未知
                    solve_count=0,
                    description="",
                    source="platform",
                ))
        return out

    def _exercise_detail(self, cid: str) -> dict:
        return self._request("GET", "/ctf/exercise", {"exerciseId": cid})

    def start_challenge(self, cid: str) -> str:
        """打开靶机: 需要初始化则 build + 轮询, 返回实例 URL (无靶机返回 "")。"""
        detail = self._exercise_detail(cid)
        if detail.get("isNeedInit"):
            self._request("POST", "/ctf/build-exercise-env", {"exerciseId": int(cid)})
            deadline = time.time() + _ENV_POLL_TIMEOUT
            while time.time() < deadline:
                time.sleep(_ENV_POLL_INTERVAL)
                detail = self._exercise_detail(cid)
                if not detail.get("isNeedCheck") and detail.get("endpoints"):
                    break
        return self._endpoint_url(detail)

    def stop_challenge(self, cid: str) -> None:
        try:
            self._request("POST", "/ctf/recover-exercise-env", {"exerciseId": int(cid)})
        except Exception:
            pass  # 回收失败不阻塞主流程

    def submit(self, cid: str, flag: str) -> SubmitResult:
        data = self._request("POST", "/answer-panel/answer",
                             {"exerciseId": int(cid), "flag": flag})
        if isinstance(data, dict) and data.get("isCorrect"):
            return SubmitResult(status="correct", data=data)
        msg = data.get("message", "") if isinstance(data, dict) else ""
        return SubmitResult(status="wrong", message=msg, data=data or {})

    def download_attachment(self, url: str, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = url.split("/")[-1].split("?")[0] or "attachment.bin"
        dest = dest_dir / name
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            f.write(resp.read())
        return dest

    # ─── 工具 ───

    @staticmethod
    def _endpoint_url(detail: dict) -> str:
        """从详情 endpoints 组装可达 URL: 优先代理 IP + proxy 端口, 否则直连 IP + 端口。"""
        eps = detail.get("endpoints") or []
        if not eps:
            return ""
        ep = eps[0]
        proxies = ep.get("proxyIps") or []
        mappings = ep.get("portMappings") or []
        if proxies and mappings:
            return f"http://{proxies[0]}:{mappings[0].get('proxy', mappings[0].get('port', ''))}"
        ips = ep.get("exposeIps") or []
        ports = ep.get("ports") or []
        if ips and ports:
            return f"http://{ips[0]}:{ports[0]}"
        return ""
