# CTF Agent 设计实施文档

> Codex 解题 + Hermes 监督 + Subagent 并行试探

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────┐
│                    Hermes (监督者)                     │
│  持续监控: tail 日志 + 读 progress.md                  │
│  主动介入: 写 guidance.md (软建议) / dead_ends.md (硬约束) │
│  看板维护: 持续更新 board.md (供 Codex 恢复上下文)        │
│  主动搜索: anysearch 搜 CTF WP/CVE/exploit/绕过技巧      │
└──────────────┬──────────────────────────────────────┘
               │ md 文档交互
               ▼
┌─────────────────────────────────────────────────────┐
│                  Codex (解题者)                       │
│  模型: GPT5.6 | reasoning: xhigh | 模式: --dangerously-bypass | 指令: AGENTS.md │
│  主会话: 侦察 -> 分析 -> 决策 -> 利用                  │
│  读 board.md 恢复状态 (compact 后也靠它)              │
│  分岔口: 异步调 branch.py spawn -> daemon 管理并行试探  │
└──────────────┬──────────────────────────────────────┘
               │ branch.py (daemon, 异步)
               ▼
┌─────────────────────────────────────────────────────┐
│              Codex Subagents (试探者)                 │
│  由 branch.py daemon 管理 (长驻, unix socket)         │
│  Codex 异步调用: spawn/status/kill/results/wait      │
│  每个: codex exec --dangerously-bypass "试这个方向: ..."  │
│  结果写 branch_result_{id}.md                        │
│  可并行多个 / 可随时 kill / 可追加新方向              │
└─────────────────────────────────────────────────────┘
```

三个角色：
- **Codex 主进程** -- 唯一决策者，负责侦察、分析、决策、利用。compact/续跑后读 board.md 恢复上下文
- **Hermes** -- 监督者，持续监控 Codex 进度，核心产出 guidance.md (主动帮 Codex 找新路、给思路给情报) 和 dead_ends.md (拦住 Codex 别走老路)，board.md 辅助维护供 Codex 恢复上下文，不干预决策
- **Codex Subagent** -- 试探者，由 branch.py daemon 长驻管理，Codex 异步 spawn/kill/查状态，并行试探分岔路口，只回带结果

---

## 2. 运行环境

| 项目 | 配置 |
|------|------|
| 宿主机 | WSL (用户当前环境) |
| Codex CLI | `npm install -g @openai/codex`，已安装 |
| Codex 模型 | GPT5.6 |
| Codex 模式 | `--dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust --ignore-rules --disable guardian_approval` (无沙箱，无审批，无安全扫描) |
| Codex 推理 | `model_reasoning_effort = "xhigh"` (config.toml + CLI -c 双保险) |
| 系统指令 | 项目根目录 `AGENTS.md` |
| Hermes | 已部署，作为监督者常驻运行 |
| 工作目录 | `~/ctf-agent/` (每次挑战一个子目录) |

Codex 不在 Docker 容器里，直接在宿主机上跑，有完整网络访问和系统权限。

---

## 3. 目录结构

```
~/ctf-agent/
├── AGENTS.md                    # Codex 系统指令 (通用原则、工具规则、输出格式)
├── branch.py                    # Subagent daemon + CLI (长驻进程, unix socket 通信)
├── monitor.py                   # Hermes 监控脚本 (纯Python, 10s轮询)
├── run.sh                       # 启动脚本 (支持 web/crypto/misc 三种题型)
├── dashboard.py                 # Web 面板后端 (HTTP + SSE, 纯 stdlib)
├── dashboard.html               # Web 面板前端 (题目选择 + 双日志面板 + 状态栏)
├── hooks/
│   └── check_guidance.py        # PostToolUse hook: 检查 guidance/dead_ends，有新内容则注入 Codex
├── hermes_monitor.md            # Hermes 监控 agent 的 prompt 指令
├── strategies/                  # 按题型拆分的攻击流程 (Codex 按需读取)
│   ├── web.md                   # Web 题攻击流程 (侦察/验证/利用 + 攻击面清单)
│   ├── crypto.md                # Crypto 题攻击流程 (RSA/AES/古典密码/哈希等)
│   └── misc.md                  # Misc 题攻击流程 (隐写/流量分析/内存取证/编码等)
├── challenges/
│   └── manual_<name>/           # 每次挑战的工作目录 (web: host_port, crypto/misc: 文件名)
│       ├── progress.md          # Codex 写: 轻量状态 (phase, target, next_steps, flags)
│       ├── board.md             # Hermes 维护: 结构化看板 (ideas + memory)
│       ├── guidance.md          # Hermes 写: 软建议 (hook 自动注入，读后清空)
│       ├── dead_ends.md         # Hermes 写: 硬约束 (hook 自动注入，读后清空)
│       ├── codex.log            # Codex 运行日志
│       ├── hermes.log           # Hermes 监控日志 (hermes chat -q 输出)
│       ├── branch.sock          # branch daemon 的 unix socket (运行时生成)
│       ├── branch_state.json    # branch daemon 持久化状态 (subagent 列表, PID, 状态)
│       ├── branch_result_{id}.md # Subagent 写: 试探结果
│       └── poc_scripts/         # PoC 脚本存档
```

---

## 4. 文档协议

### 4.1 progress.md (Codex -> Hermes)

Codex 主进程维护，每次工具调用后更新。轻量状态，结构化看板在 board.md。

```markdown
## Target
- URL: http://xxx:xxx
- Background: 题目背景信息
- Start Time: 2026-07-26T14:00:00

## Current Phase
exploitation

## Next Steps
1. 验证 SQL 注入 UNION 绕过
2. 尝试 /render 的 SSTI

## Key Artifacts
- /tmp/sqli_poc.py: SQL注入验证脚本

## Flags Found
(无)
```

注意：Attack Tree 和 Dead Ends 已移到 board.md，由 Hermes 维护。
progress.md 只保留 Codex 自己需要快速回看的状态。

### 4.2 guidance.md (Hermes -> Codex, 软建议)

Hermes 写，Codex 可以 ignore。PostToolUse hook 自动注入，读后清空。

```markdown
## [2026-07-26T14:15:00] 供参考
你已经在 SQL注入 上花了15分钟，UNION 一直被 WAF 拦。
可以试试 stacked queries 或者 out-of-band 方式。

## [2026-07-26T14:25:00] 供参考
我搜了一下这个 CMS 的版本，发现有个已知的 SSRF 漏洞在 /api/proxy，
如果 SQLi 走不通可以考虑这个方向。
```

### 4.3 dead_ends.md (Hermes -> Codex, 硬约束)

Hermes 写，Codex 必须遵守。PostToolUse hook 自动注入，读后清空。格式严格。

```markdown
## [2026-07-26T14:30:00] DO NOT RETRY
- 方向: 路径扫描 /admin
  原因: ffuf 500词字典 + 手动测试，全部 404
  时间: 14:10-14:20
  证据: `ffuf -u http://xxx/FUZZ -w common.txt` -> 0 hits

- 方向: 默认密码 admin:admin
  原因: POST /login 返回 401，尝试3次
  证据: `curl -X POST -d 'user=admin&pass=admin' http://xxx/login` -> 401
```

### 4.4 branch.py 通信协议 (Codex -> daemon)

Codex 不再写 branch_request.md，而是直接调 branch.py 子命令与 daemon 交互。
daemon 是长驻进程，绑定 `{work_dir}/branch.sock` (unix socket)，所有状态在内存维护。
子命令是 thin client，连接 socket 发 JSON 请求，收 JSON 响应。

```
# 启动 daemon (run.sh 自动拉起，Codex 不需要管)
python3 branch.py daemon --work-dir <dir>

# Codex 调用的子命令:
python3 branch.py spawn  --work-dir <dir> --name "方向名" --prompt "..." [--timeout 300]
  -> 返回 {"id": "branch_001", "pid": 12345}

python3 branch.py status --work-dir <dir>
  -> 返回所有 subagent 状态表:
     [{"id":"branch_001","name":"SQLi","status":"running","elapsed":"120s","timeout":"300s"},
      {"id":"branch_002","name":"SSTI","status":"done","exit_code":0,"result":"branch_result_branch_002.md"}]

python3 branch.py kill   --work-dir <dir> <id>
  -> 返回 {"id":"branch_001","status":"killed"}

python3 branch.py results --work-dir <dir> [id]
  -> 返回已完成 subagent 的结果摘要 (读 branch_result_{id}.md)

python3 branch.py wait   --work-dir <dir> [id] [--timeout 60]
  -> 阻塞等某个或全部 subagent 完成 (Codex 需要同步等时用)

python3 branch.py shutdown --work-dir <dir>
  -> kill 所有 subagent + 关闭 daemon (挑战结束时用)
```

daemon 内部循环:
```
while running:
    1. select 监听 branch.sock (等子命令请求)
    2. os.waitpid(WNOHANG) 回收已结束的 subagent，获取退出码
    3. 检查超时 -> SIGTERM kill + 更新状态
    4. subagent 完成 -> 更新状态 + 持久化 branch_state.json
    5. 处理子命令请求 (spawn/kill/status/results/wait/shutdown)
```

### 4.5 branch_result_{id}.md (Subagent -> Codex)

每个 subagent 完成后写结果文件。

```markdown
## Branch Result
direction: SQL注入 UNION 绕过
subagent_id: branch_001
timestamp: 2026-07-26T14:25:00
duration: 5min
status: FEASIBLE

### 发现
- 内联注释 /*!50000UNION*/ 可以绕过 WAF
- `id=1/*!50000UNION*//*!50000SELECT*/1,2,3` 返回 200
- 存在 3 个字段，字段 2 有回显

### 命令和结果
`curl 'http://xxx/search.php?id=1/*!50000UNION*//*!50000SELECT*/1,2,3'` -> 200, body 含 "2"

### 结论
SQL注入可行，建议主进程深入利用: 提取 flag 表数据

### PoC 脚本
- /tmp/sqli_bypass.py
```

### 4.6 board.md (Hermes 维护, Codex 只读)

借鉴 BreachWeave 的结构化看板。progress.md 在 Codex 上下文里，compact 会丢。
board.md 由 Hermes 独立维护，不受 compact 影响。Codex compact 或续跑后读 board.md 就能恢复"我在试什么、已知什么、什么路走不通"。

Hermes 从 progress.md + codex.log 中提取信息，维护两块板：ideas（攻击假设）和 memory（持久事实）。

```markdown
# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|--------|---------|
| I01 | testing | SQL注入 in /search.php (id参数, UNION绕过) | 内联注释可绕WAF, 字段2有回显 | 14:25 |
| I02 | pending | SSTI in /render (name参数) | - | 14:20 |
| I03 | failed | 命令注入 in /api/exec | 分号被过滤, 尝试5种绕过均失败 | 14:15 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| M01 | fact | 目标是 Craft CMS 5.x | recon | 14:05 |
| M02 | evidence | /search.php id参数单引号触发500 | curl测试 | 14:10 |
| M03 | failure | /admin 路径404, ffuf 500词无发现 | ffuf扫描 | 14:08 |
| M04 | hint | WAF过滤UNION/SELECT关键词 | 多次测试 | 14:20 |
```

#### Ideas 生命周期

状态机：`pending` -> `testing` -> `verified` / `failed` / `skipped`

- **pending**: 新提出的攻击假设，尚未验证
- **testing**: 正在验证中
- **verified**: 已确认可行，有决定性证据
- **failed**: 已排除，有明确证据否定
- **skipped**: 暂时跳过，优先级低或依赖其他方向

**保守 failed 判定（借鉴 BreachWeave 三问法）**：

把 idea 标记为 failed 前，Hermes 必须连续自问：
1. 这次失败否定的是整条路线，还是只否定了某个 payload/编码/子分支？
2. 这条路线是否仍存在合理变体、上下文条件或未验证前提？
3. 这次更适合把失败边界写进 memory，而不是关闭整条主线吗？

任何一个问题不能明确排除，就保持 testing 或回到更窄的 pending。

#### Memory 类型

- **fact**: 确认的事实（技术栈、版本、配置）
- **evidence**: 验证结果（某参数存在注入、某端口开放）
- **failure**: 失败边界（某方向不可行、某防御机制存在）
- **hint**: 题目提示或外部搜到的信息

#### 容量约束

- Memory 保持在 **12 条以内**
- Ideas 保持在 **8 条以内**
- 超限时优先 merge/update/delete，再考虑 add
- 同主题的新证据优先改写旧记录，不新增近义记录

#### Hermes 看板维护规则

默认动作优先级（借鉴 BreachWeave Observer）：
`NO_CHANGE` > `update existing` > `delete superseded` > `add new`

1. 先闭环已有主线：progress.md 的最新结果是否证实/证伪了某条 idea
2. 能闭环就更新 idea 的 status + result
3. 只是子分支失败，先记 failure memory，不判死主线
4. 确实打开新方向才新增 idea
5. 既没新方向也没更强结论，不动看板

---

## 5. AGENTS.md -- Codex 系统指令

放在 `~/ctf-agent/AGENTS.md`，Codex 每次启动自动读取。

### 5.1 核心原则

```
你是 CTF 自动解题 Agent。目标: 找到并提交 FLAG。

决策权完全在你手里。你有一个监督者 (Hermes)，它会通过文件给你建议:
- guidance.md: 软建议，hook 自动注入，你可以参考也可以忽略
- dead_ends.md: 硬约束，hook 自动注入，已经验证不可行的方向，禁止重试
- board.md: 结构化看板 (ideas + memory)，Hermes 维护，你只读

guidance.md 和 dead_ends.md 不需要主动读，PostToolUse hook 会在每次工具调用后自动检查并注入。
启动时、compact 后、切换路线前，先读 board.md 恢复状态。
每次工具调用后更新 progress.md。
遇到分岔路口异步调 branch.py spawn 请求并行试探。
```

### 5.2 攻击流程

```
1. 侦察阶段 (5-10min)
   - 读 board.md 了解已有 ideas 和 memory
   - curl 探测目标，识别技术栈
   - 端口扫描、目录扫描
   - 识别所有入口点
   - 发现 2+ 攻击向量时，调 branch.py spawn 并行试探

2. 验证阶段 (每个向量 3-5min)
   - 选最高优先级向量全力推进
   - 单次失败不换方向
   - 同一命令参数微调不超 3 次
   - 同类操作连续 3 次无新发现 -> 换方向

3. 利用阶段 (充分投入)
   - 对确认漏洞深入利用
   - 拿到 RCE 先读 flag (cat /flag*、find / -name "flag*"、env)
   - 发现 flag 立即输出

4. 停滞处理
   - 读 board.md 看 Hermes 维护的当前状态
   - guidance.md / dead_ends.md 通过 hook 自动注入，不需要主动读
   - 换完全不同的攻击方向
```

### 5.3 工具使用规则

```
- 所有命令通过 shell 执行 (curl/nmap/sqlmap/ffuf/python3 等)
- 长输出重定向到文件 (cmd > /tmp/out.txt 2>&1)，只回传摘要
- Python PoC 用 python3 执行
- 禁止交互式命令 (sqlmap 交互式、nc -l 等)
- 禁止暴力破解密码 (效率太低)
```

### 5.4 Subagent 使用规则

```
遇到分岔路口 (2+ 可行方向需要验证):
1. spawn: python3 branch.py spawn --work-dir . --name "方向名" --prompt "..."
   - 可以连续 spawn 多个方向，每个立即返回 subagent_id
   - 主会话不被阻塞，继续自己的主攻方向
2. 查状态: python3 branch.py status --work-dir .
   - 定期查，了解哪些完成了、哪些还在跑
3. 读结果: python3 branch.py results --work-dir . <id>
   - FEASIBLE -> 选这个方向继续深入，可以再 spawn 新 subagent 深入利用
   - INFEASIBLE -> 跳过
4. kill: python3 branch.py kill --work-dir . <id>
   - 某方向已 FEASIBLE，kill 掉不需要的其他 subagent 省时间
   - 某方向跑太久没结果，kill 换方向
5. 等待: python3 branch.py wait --work-dir . [--timeout 60]
   - 需要同步等结果时用，但尽量少用，保持异步
6. 不要在主会话里试分岔路口，主会话只做决策和深度利用
7. 挑战结束: python3 branch.py shutdown --work-dir .
```

### 5.5 输出格式

```
最终输出结构化 JSON:
{
  "solved": boolean,
  "flag": string | null,
  "summary": string,
  "confidence": 0.0-1.0
}

FLAG 格式: flag{...} / FLAG{...} / ctf{...} / CTF{...}
```

---

## 6. branch.py -- Subagent Daemon

### 6.1 架构

```python
#!/usr/bin/env python3
"""
Subagent daemon + CLI。
长驻进程管理所有 Codex subagent，通过 unix socket 接收子命令。

用法:
  # 启动 daemon (run.sh 自动拉起)
  python3 branch.py daemon --work-dir <dir>

  # 子命令 (Codex 调用，通过 socket 通信)
  python3 branch.py spawn   --work-dir <dir> --name "..." --prompt "..." [--timeout 300]
  python3 branch.py status  --work-dir <dir>
  python3 branch.py kill    --work-dir <dir> <id>
  python3 branch.py results --work-dir <dir> [id]
  python3 branch.py wait    --work-dir <dir> [id] [--timeout 60]
  python3 branch.py shutdown --work-dir <dir>
"""

import sys, os, json, subprocess, time, signal, socket, select
from pathlib import Path

CODEX_CMD = os.environ.get("CODEX_CMD", "codex")  # 可用环境变量覆盖
DEFAULT_TIMEOUT = 300  # 单个 subagent 默认 5 分钟

# ─── daemon 状态 ───
# subagent 记录结构:
# {
#   "id": "branch_001",
#   "name": "SQL注入 UNION 绕过",
#   "pid": 12345,
#   "started_at": 1722100000,
#   "timeout": 300,
#   "status": "running",  # running / done / timeout / killed / crashed
#   "exit_code": null,
#   "result_file": "branch_result_branch_001.md"
# }

class BranchDaemon:
    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.sock_path = work_dir / "branch.sock"
        self.state_path = work_dir / "branch_state.json"
        self.subagents = {}  # id -> subagent record
        self.counter = 0

    def run(self):
        """daemon 主循环"""
        # 绑定 unix socket
        if self.sock_path.exists():
            self.sock_path.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self.sock_path))
        srv.listen(8)
        srv.setblocking(False)

        # 恢复已有状态 (daemon 重启场景)
        self._restore_state()

        while True:
            # select: 同时监听 socket 和子进程
            readable, _, _ = select.select([srv], [], [], 1.0)

            # 1. 处理子命令请求
            if srv in readable:
                conn, _ = srv.accept()
                self._handle_command(conn)

            # 2. 回收已结束的子进程
            self._reap_subagents()

            # 3. 检查超时
            self._check_timeouts()

            # 4. 持久化状态
            self._persist_state()

    def _handle_command(self, conn):
        """处理一个子命令请求"""
        data = conn.recv(65536)
        req = json.loads(data)
        cmd = req["cmd"]
        if cmd == "spawn":
            resp = self._spawn(req["name"], req["prompt"], req.get("timeout", DEFAULT_TIMEOUT))
        elif cmd == "status":
            resp = self._status()
        elif cmd == "kill":
            resp = self._kill(req["id"])
        elif cmd == "results":
            resp = self._results(req.get("id"))
        elif cmd == "wait":
            resp = self._wait(req.get("id"), req.get("timeout", 60))
        elif cmd == "shutdown":
            resp = self._shutdown()
        else:
            resp = {"error": f"unknown command: {cmd}"}
        conn.sendall(json.dumps(resp).encode())
        conn.close()

    def _spawn(self, name, prompt, timeout):
        """spawn 一个 Codex subagent (不阻塞)"""
        self.counter += 1
        sid = f"branch_{self.counter:03d}"
        result_file = f"branch_result_{sid}.md"

        full_prompt = (
            f"{prompt}\n\n"
            f"完成后将结果写入 {self.work_dir}/{result_file}:\n"
            f"## Branch Result\n"
            f"direction: {name}\n"
            f"subagent_id: {sid}\n"
            f"status: FEASIBLE | INFEASIBLE\n"
            f"### 发现\n...\n"
            f"### 命令和结果\n...\n"
            f"### 结论\n..."
        )

        proc = subprocess.Popen(
            [CODEX_CMD, "exec", "--dangerously-bypass-approvals-and-sandbox",
             "--dangerously-bypass-hook-trust", "--ignore-rules",
             "--disable", "guardian_approval",
             "-c", "model_reasoning_effort=xhigh", full_prompt],
            stdout=open(self.work_dir / f"{sid}.log", "w"),
            stderr=subprocess.STDOUT,
            cwd=str(self.work_dir),
        )

        self.subagents[sid] = {
            "id": sid, "name": name, "pid": proc.pid,
            "started_at": time.time(), "timeout": timeout,
            "status": "running", "exit_code": None,
            "result_file": result_file,
        }
        return {"id": sid, "pid": proc.pid}

    def _reap_subagents(self):
        """非阻塞回收已结束的子进程"""
        for sid, sa in self.subagents.items():
            if sa["status"] != "running":
                continue
            pid = sa["pid"]
            try:
                waited_pid, status = os.waitpid(pid, os.WNOHANG)
                if waited_pid == pid:
                    if os.WIFEXITED(status):
                        sa["exit_code"] = os.WEXITSTATUS(status)
                        sa["status"] = "done" if sa["exit_code"] == 0 else "crashed"
                    elif os.WIFSIGNALED(status):
                        sa["status"] = "killed"
                        sa["exit_code"] = -os.WTERMSIG(status)
                    sa["finished_at"] = time.time()
            except ChildProcessError:
                # 进程已不在 (被外部 kill)
                sa["status"] = "killed"

    def _check_timeouts(self):
        """检查超时，主动 kill"""
        now = time.time()
        for sid, sa in self.subagents.items():
            if sa["status"] != "running":
                continue
            elapsed = now - sa["started_at"]
            if elapsed > sa["timeout"]:
                os.kill(sa["pid"], signal.SIGTERM)
                sa["status"] = "timeout"
                sa["finished_at"] = now

    def _persist_state(self):
        """持久化到 branch_state.json (daemon 崩溃后可恢复)"""
        data = json.dumps(self.subagents, ensure_ascii=False, indent=2)
        self.state_path.write_text(data)

    def _restore_state(self):
        """从 branch_state.json 恢复 (daemon 重启场景)"""
        if self.state_path.exists():
            self.subagents = json.loads(self.state_path.read_text())
            self.counter = len(self.subagents)
            # 检查 running 的是否还活着
            for sa in self.subagents.values():
                if sa["status"] == "running":
                    try:
                        os.kill(sa["pid"], 0)
                    except ProcessLookupError:
                        sa["status"] = "killed"

    def _status(self):
        """返回所有 subagent 状态"""
        now = time.time()
        result = []
        for sa in self.subagents.values():
            item = {"id": sa["id"], "name": sa["name"], "status": sa["status"]}
            if sa["status"] == "running":
                item["elapsed"] = f"{int(now - sa['started_at'])}s"
                item["timeout"] = f"{sa['timeout']}s"
            else:
                item["exit_code"] = sa.get("exit_code")
                item["result"] = sa.get("result_file")
            result.append(item)
        return {"subagents": result}

    def _kill(self, sid):
        """kill 指定 subagent"""
        sa = self.subagents.get(sid)
        if not sa:
            return {"error": f"unknown id: {sid}"}
        if sa["status"] == "running":
            os.kill(sa["pid"], signal.SIGTERM)
            sa["status"] = "killed"
            sa["finished_at"] = time.time()
        return {"id": sid, "status": sa["status"]}

    def _results(self, sid=None):
        """读结果文件"""
        if sid:
            sa = self.subagents.get(sid)
            if not sa:
                return {"error": f"unknown id: {sid}"}
            path = self.work_dir / sa["result_file"]
            return {"id": sid, "content": path.read_text() if path.exists() else None}
        # 返回所有已完成的结果
        results = []
        for sa in self.subagents.values():
            if sa["status"] in ("done", "timeout", "killed", "crashed"):
                path = self.work_dir / sa["result_file"]
                results.append({
                    "id": sa["id"], "name": sa["name"],
                    "status": sa["status"],
                    "has_result": path.exists(),
                })
        return {"results": results}

    def _wait(self, sid=None, timeout=60):
        """阻塞等待某个或全部 subagent 完成"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._reap_subagents()
            if sid:
                sa = self.subagents.get(sid)
                if sa and sa["status"] != "running":
                    return {"id": sid, "status": sa["status"]}
            else:
                if all(sa["status"] != "running" for sa in self.subagents.values()):
                    return {"status": "all_done"}
            time.sleep(1)
        return {"status": "timeout"}

    def _shutdown(self):
        """kill 所有 subagent + 关闭 daemon"""
        for sa in self.subagents.values():
            if sa["status"] == "running":
                os.kill(sa["pid"], signal.SIGTERM)
                sa["status"] = "killed"
        self._persist_state()
        return {"status": "shutdown"}

# ─── CLI thin client ───
def cli_client(work_dir, cmd, **kwargs):
    """连接 daemon socket，发送命令，返回响应"""
    sock_path = Path(work_dir) / "branch.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(sock_path))
    sock.sendall(json.dumps({"cmd": cmd, **kwargs}).encode())
    resp = sock.recv(65536)
    sock.close()
    return json.loads(resp)

if __name__ == "__main__":
    # 解析命令行参数，daemon 模式启动 daemon，否则走 CLI client
    ...
```

### 6.2 关键设计点

- **长驻 daemon**: 单进程内存维护所有 subagent 状态，零竞争
- **unix socket 通信**: 子命令是 thin client，连接 socket 发 JSON 收 JSON
- **非阻塞 spawn**: Popen 启动后立即返回 subagent_id，主进程不被阻塞
- **主动进程回收**: daemon 用 os.waitpid(WNOHANG) 非阻塞回收，拿到退出码区分 done/crashed
- **主动超时管理**: daemon 内存跟踪，到点 SIGTERM + 更新状态，不依赖外部 timeout 命令
- **状态持久化**: branch_state.json，daemon 崩溃后可恢复，检查 running 的进程是否还活着
- **结果回收**: subagent 自己写 branch_result_{id}.md，主进程通过 `branch.py results` 读取
- **异步管理**: Codex 可以 spawn 后继续干别的事，定期 status 查状态，按需 kill/新建/深入

---

## 7. Hermes 监控逻辑

### 7.1 监控架构

monitor.py 是 Hermes 的眼睛，不是异常检测器。它持续 tail codex.log，每次输出日志增量 + progress 状态。Hermes agent 看到输出后，用自己的理解力判断该不该介入。

```
Layer 1: monitor.py (纯 Python，10s 轮询)
  - tail codex.log，输出日志增量 (基于 last_log_offset)
  - 解析 progress.md，提取 phase/next_steps/flags/url
  - 读 dead_ends.md 当前内容
  - 检查 flag (Flags Found 段)
  - 检查日志停滞 (is_stale) 和超时 (is_timeout)
  - 无新日志 -> 静默，不调 agent
  - 有新日志 -> 输出 JSON，触发 Layer 2

Layer 2: Hermes agent (按需触发)
  - 收到 monitor.py 输出的日志增量
  - 读 hermes_monitor.md 获取详细指令
  - 用 LLM 理解力判断 Codex 在干什么、该不该介入
  - 介入方式: 写 guidance.md (帮找新路) / dead_ends.md (拦住) / board.md (恢复上下文)
  - 主动搜索: anysearch 搜 CTF WP/CVE/绕过技巧
```

实现: run.sh 中 background bash 循环，每 10s 跑 monitor.py，有输出时 `hermes chat -q` 调 agent。不依赖 gateway/cronjob。

### 7.2 卡住模式检测

| 模式 | 检测方法 | 介入方式 |
|------|----------|----------|
| 同一方向重复 | progress.md 的 Next Steps 连续3轮不变 | dead_ends.md: 拦住该方向 |
| 参数微调循环 | 日志中同一命令出现3+次（参数微调） | dead_ends.md: 拦住该参数方向 |
| 重复已失败路径 | 当前 Next Steps 出现在 dead_ends.md | dead_ends.md: 追加硬约束 |
| 无输出间隔 | 日志最后一条 >5分钟前 | dead_ends.md: 拦住当前方向 |
| 有可搜线索 | progress.md 出现技术栈/题目类型/版本号等 | anysearch 搜同类 CTF WP/CVE/绕过技巧，结果写 guidance.md |
| idea 长时间 testing | board.md 中某 idea 状态 testing 超过15分钟 | dead_ends.md: 拦住该 idea 方向 |
| memory 容量超限 | board.md memory >12 条或 ideas >8 条 | Hermes 自动 merge/delete 低价值条目 |

### 7.3 Hermes 介入规则

**软建议 (guidance.md) -- 主动帮 Codex 找新路**:
- Hermes 主动给思路和情报: 搜到同类 CTF WP、搜到 CVE/exploit、分析出没试过的攻击面、搜到绕过技巧
- 一切手段都服务于"帮 Codex 找新路"这一个目的
- 格式: "搜到X，供参考。Y 方向可能值得一试。"
- 不写: "你应该做X" (避免干预决策)
- Codex 可以完全忽略

**硬约束 (dead_ends.md) -- 拦住 Codex 别走老路**:
- 卡住了 (方向停滞/命令重复/无输出) 和走死路了 (重复已验证失败路径) 都写这里
- 格式: 方向 + 原因 + 证据 + 时间
- Codex 必须遵守，不可忽略

---

## 8. 启动流程

### 8.1 run.sh 启动脚本

```bash
#!/bin/bash
# 用法:
#   ./run.sh --type web --url "http://target:8080" --hint "SQL注入"
#   ./run.sh --type crypto --attachment "/path/to/file.zip" --hint "RSA"
#   ./run.sh --type misc --attachment "/path/to/file.zip" --hint "隐写"
# 含自动续跑: Codex 退出后如果没找到 flag，自动重启继续

# 参数解析
CHALLENGE_TYPE="" TARGET_URL="" ATTACHMENT="" HINT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --type)       CHALLENGE_TYPE="$2"; shift 2 ;;
        --url)        TARGET_URL="$2"; shift 2 ;;
        --attachment) ATTACHMENT="$2"; shift 2 ;;
        --hint)       HINT="$2"; shift 2 ;;
        *) exit 1 ;;
    esac
done

# 工作目录: web 用 host_port, crypto/misc 用文件名
case "$CHALLENGE_TYPE" in
    web)        WORK_DIR="challenges/manual_$(echo $TARGET_URL | sed 's|https\?://||;s|[:/]|_|g')" ;;
    crypto|misc) WORK_DIR="challenges/manual_$(basename "$ATTACHMENT" | sed 's/\.[^.]*$//')"
                 cp "$ATTACHMENT" "$WORK_DIR/"  # 复制附件到工作目录 ;;
esac

# 初始化 progress.md / board.md / guidance.md / dead_ends.md / hermes.log
# (progress.md 按题型区分初始 Next Steps)
# ...

# 按题型构建 Codex prompt
case "$CHALLENGE_TYPE" in
    web)        CODEX_PROMPT="目标: $TARGET_URL\n背景: $HINT\n先读 strategies/web.md ..." ;;
    crypto|misc) CODEX_PROMPT="附件: $ATTACHMENT\n背景: $HINT\n先读 strategies/$CHALLENGE_TYPE.md ..." ;;
esac

# 启动 branch daemon + Hermes 监控循环 (Hermes 输出写 hermes.log)
# ...

# 自动续跑循环
INTERRUPTED=0
trap 'INTERRUPTED=1' SIGINT SIGTERM  # Ctrl+C 不续跑

RETRY=0
while [ $RETRY -lt $MAX_RETRIES ] && [ $INTERRUPTED -eq 0 ]; do
    codex exec --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust \
      --ignore-rules --disable guardian_approval -c model_reasoning_effort="xhigh" \
      "$CODEX_PROMPT" > codex.log 2>&1 || true

    if [ $INTERRUPTED -eq 1 ]; then break; fi

    # 检查 progress.md 的 Flags Found 段
    FLAGS=$(awk '/^## *Flags Found/{f=1;next} /^##/{f=0} f' progress.md | grep -v '^(无)' | grep -v '^$')
    if [ -n "$FLAGS" ]; then echo "FLAG FOUND: $FLAGS"; break; fi

    RETRY=$((RETRY+1)); sleep 3
done
```

### 8.2 完整启动流程

**方式一: Web 面板 (推荐)**

```
1. cd ~/ctf-agent
2. python3 dashboard.py
3. 浏览器打开 http://localhost:8080
4. 选择题目类型，填写 URL 或附件路径 + 背景信息，点击启动
5. 页面实时展示: 左侧 Codex 日志，右侧 Hermes 日志，底部状态栏
6. 点击停止或等 flag 自动找到
```

**方式二: 命令行**

```
1. cd ~/ctf-agent
2. ./run.sh --type web --url "http://target:port" --hint "背景信息"
   ./run.sh --type crypto --attachment "/path/to/file.zip" --hint "RSA"
3. Codex 启动解题，Hermes 开始持续监控
4. tail -f challenges/manual_<name>/codex.log 看实时日志
5. flag 找到后 Codex 输出到 progress.md 的 Flags Found 段
```

---

## 9. Compact 恢复策略

Codex CLI 有自动 compact（上下文压缩）。compact 后 Codex 会丢失部分上下文。

**核心恢复机制：board.md**

board.md 由 Hermes 独立维护，不受 Codex compact 影响。compact 后 Codex 读 board.md 即可恢复：
- ideas 看板知道当前哪些方向在测、哪些已验证、哪些已失败
- memory 看板知道关键事实、证据、失败边界、外部提示
- guidance.md / dead_ends.md 在 compact 后通过 hook 自动注入，不需要主动读

AGENTS.md 中写入恢复指令：
```
compact 后恢复:
1. 读 board.md 获取结构化看板 (ideas + memory) -- 最重要
2. 读 progress.md 获取当前 phase 和 next steps
3. branch.py status 查看是否有还在跑的 subagent
4. guidance.md / dead_ends.md 通过 hook 自动注入，不需要主动读
5. 从 Current Phase 和 Next Steps 继续
```

**Hermes 在 compact 期间的职责**：

Hermes 的 10 秒轮询不依赖 Codex 上下文，compact 期间照常运行。
如果检测到 Codex compact（日志特征），Hermes 可以主动更新 board.md，
把 progress.md 里的最新进展同步到看板，确保 Codex 恢复时看到最新状态。

先跑这个机制，观察效果。如果 board.md 质量足够好，不需要额外的 ProgressCompiler。

---

## 10. Hermes 集成实现

### 10.1 background bash 循环 (已实现)

run.sh 中直接启动后台 bash 循环，不依赖 Hermes gateway/cronjob：

```bash
bash -c '
    while true; do
        OUTPUT=$(python3 "$SCRIPT_DIR/monitor.py" --work-dir "$WORK_DIR" 2>/dev/null)
        if [ -n "$OUTPUT" ]; then
            echo "=== [$(date "+%H:%M:%S")] Hermes agent 被触发 ===" >> "$WORK_DIR/hermes.log"
            hermes chat -q "你是 CTF 监督者。以下是 monitor.py 收集的 Codex 最新进展:
$OUTPUT
请读 $SCRIPT_DIR/hermes_monitor.md 获取详细指令，然后按指令执行。
执行完毕后回复简短摘要。" -t terminal,file,web,search --quiet >> "$WORK_DIR/hermes.log" 2>&1 || true
            echo "" >> "$WORK_DIR/hermes.log"
        fi
        sleep 10
    done
' &
```

### 10.2 分层职责

**monitor.py (纯 Python，无 LLM)**：
- tail codex.log 输出日志增量
- 解析 progress.md 提取关键状态
- 检测 flag (Flags Found 段)、日志停滞、超时
- 无新日志 -> 静默，不调 agent

**Hermes agent (LLM，按需触发)**：
- 看日志增量理解 Codex 在干什么
- 主动搜索 CTF WP/CVE/绕过技巧 -> 写 guidance.md
- 判断卡住/走死路 -> 写 dead_ends.md
- 维护 board.md (供 Codex compact/续跑恢复)

### 10.3 PostToolUse hook 实时注入 (已实现)

Codex 的 guidance.md / dead_ends.md 通过 PostToolUse hook 实时注入，不需要 Codex 主动读：

- hook 配置: `~/.codex/hooks.json` (全局，因为工作目录不固定)
- hook 脚本: `~/ctf-agent/hooks/check_guidance.py`
- 触发时机: 每次 Bash 工具调用后
- 机制: 检查 guidance.md / dead_ends.md，有内容则通过 additionalContext 注入给 Codex，然后清空文件 (读后清空)
- 无内容时静默退出，不占上下文
- run.sh 已有 `--dangerously-bypass-hook-trust` 跳过 hook 信任检查

这样 Hermes 写的指导能在 Codex 单轮运行中实时送达，不需要等续跑。

---

## 11. 待实现清单

### 11.1 第一阶段 -- 核心骨架 (已完成)

| 序号 | 任务 | 文件 | 状态 |
|------|------|------|------|
| 1 | 安装 Codex CLI | `npm install -g @openai/codex` | done |
| 2 | 写 AGENTS.md (通用指令 + 题型策略引导) | `~/ctf-agent/AGENTS.md` | done |
| 3 | 写 run.sh (支持 web/crypto/misc + 自动续跑 + Hermes 监控) | `~/ctf-agent/run.sh` | done |
| 4 | 写 branch.py (daemon + CLI: spawn/status/kill/results/wait/shutdown) | `~/ctf-agent/branch.py` | done |
| 5 | 写 monitor.py (10s轮询+日志增量+flag检测+停滞检测) | `~/ctf-agent/monitor.py` | done |
| 6 | PostToolUse hook (guidance/dead_ends 实时注入) | `~/ctf-agent/hooks/check_guidance.py` | done |
| 7 | 题型策略拆分 (strategies/web.md, crypto.md, misc.md) | `~/ctf-agent/strategies/` | done |
| 8 | Web 面板 (HTTP + SSE + 双日志面板 + 启停) | `~/ctf-agent/dashboard.py` + `dashboard.html` | done |
| 9 | 端到端测试 | branch.py 9/9, monitor.py 4/4, hook 实测, dashboard API 5/5 | done |

### 11.2 第二阶段 -- 看板质量优化

| 序号 | 任务 | 说明 |
|------|------|------|
| 7 | board.md 维护逻辑细化 | ideas 状态机 + memory 类型 + 容量约束 |
| 8 | 保守 failed 判定 | 三问法实现 |
| 9 | compact 检测 + board.md 主动同步 | 检测日志特征，同步 progress 到看板 |
| 10 | dead_ends.md 自动维护 | 检测重复路径 |
| 11 | 监控策略调优 | 根据实战调整卡住检测阈值 |

### 11.3 后话

| 任务 | 说明 |
|------|------|
| 多题并发 | Planner 调度，借鉴 BreachWeave ChallengeManager |
| 题间共享 memory | 跨题知识迁移 |
| skills 库 | 常见漏洞利用模板 |
| Docker 工具链 | sqlmap/katana/ffuf 容器化 |
| 比赛平台对接 | API 提交 flag、获取题目列表 |

---

## 12. 和已有方案对比

| 维度 | newmapta | CHYing | BreachWeave | 本方案 |
|------|----------|--------|-------------|--------|
| 解题 Agent | CrewAI 多 Agent (4角色) | Claude Code 单 Agent | pi-coding-agent (Docker隔离) | Codex 单 Agent |
| 监督者 | 无 | 系统 Guidance Loop | Observer sidecar (容器内) | Hermes 独立 Agent |
| 看板/状态 | 无 | progress.md + findings.log | 结构化 ideas+memory (Observer维护) | 结构化 board.md (Hermes维护) |
| 并行试探 | 无 | 无 | 无 (单Solver单线推进) | 异步并行 (branch.py daemon, 可 spawn/kill/追加) |
| 上下文管理 | Pebble 进程池 | ProgressCompiler | Observer durable memory | board.md + Codex 自带 compact |
| 状态持久化 | 无 | progress.md + findings.log | JSONL session + 看板文件 | board.md + progress.md |
| 人机协同 | 无 | human_guidance.md (读后清空) | 无 | Hermes 自动 + PostToolUse hook 实时注入 |
| 知识库 | ChromaDB RAG (58文档) | kb_search MCP | 手写 skills + Docker军火库 | 按需 anysearch |
| 工具 | Docker 隔离 | Docker Kali | Docker Kali + 20手写skill | 宿主机直接执行 |
| 竞赛适配 | 批量调度 | 重试+Session Rotation | Planner多题调度+自动续跑 | 自动续跑 (后续加多题) |
| 外部搜索 | 无 | 无 | security_kimi_search | anysearch (Hermes主动搜WP/CVE/绕过技巧) |
| 建议分级 | 无 | 无 | 单一 steer | 软建议 + 硬约束 |
| 监控面板 | 无 | dashboard (8080) | 无 | Web 面板 (双日志 SSE 实时推送) |

本方案的核心优势：
1. **异步并行试探** -- BreachWeave、newmapta 和 CHYing 都是单线程探索，本方案遇到分岔路口可以异步 spawn 多个 subagent 并行试，某个 FEASIBLE 后可立即 kill 其他省时间，主进程不被阻塞
2. **监督者独立 + 有外部搜索** -- Hermes 不在 Codex 上下文里，不占用注意力；且能主动搜同类 CTF WP/CVE/绕过技巧帮 Codex 找新路，BreachWeave 的 Observer 做不到
3. **结构化看板 + 建议分级** -- 借鉴 BreachWeave 的 ideas/memory 看板，但由独立的 Hermes 维护（不在容器内）；且有软建议+硬约束分级，BreachWeave 只有单一 steer
4. **架构简单** -- 不需要 Docker/MCP，Codex 直接在宿主机跑，减少一层抽象
5. **Codex GPT5.6 本身强** -- 单打 CTF 能力极强，架构层面做减法
6. **PostToolUse hook 实时注入** -- 借鉴 CHYing 读后清空机制，Hermes 写的指导在 Codex 单轮运行中实时送达，不需要等续跑

本方案的潜在风险：
1. Codex CLI 的 AGENTS.md 能力有限，可能不如 Claude Code 的 CLAUDE.md 灵活
2. Codex --dangerously-bypass 模式无安全限制，CTF 靶场可能有恶意反弹
3. Subagent 是独立 Codex 进程，不共享上下文，结果回收依赖文件；daemon 崩溃可从 branch_state.json 恢复但需测试可靠性
4. guidance.md / dead_ends.md 读后清空，历史内容只在 board.md 中保留

---

## 13. 关键设计决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 解题 Agent | Codex CLI | GPT5.6 推理能力强，--dangerously-bypass 无限制 |
| 监督 Agent | Hermes | 已部署，有 anysearch/terminal/cronjob 能力 |
| Subagent spawn | 长驻 daemon (unix socket) | 异步管理: spawn不阻塞/可kill/可追加/主动超时/进程回收/状态持久化，无状态子命令方案有超时管理缺失、退出码丢失、state文件竞争等硬伤 |
| 监督模式 | 持续监控 | 预算充足，监督者应主动介入 |
| 交互方式 | md 文档 | CHYing 验证可行，简单可靠 |
| 建议分级 | 硬约束 + 软建议 | 决策权在 Codex，Hermes 只设护栏 |
| 结构化看板 | board.md (Hermes维护) | 借鉴 BreachWeave，独立于 Codex compact，不丢状态 |
| ideas failed 判定 | 保守三问法 | 借鉴 BreachWeave，避免过早关闭可行路线 |
| 看板容量 | memory≤12, ideas≤8 | 借鉴 BreachWeave，防膨胀，强制精炼 |
| 自动续跑 | ralph-loop 最多10轮 | 借鉴 BreachWeave，Codex 退出后自动重启 |
| compact 恢复 | 先靠 board.md | Hermes 独立维护不受 compact 影响，先不加 ProgressCompiler |
| 不用 Docker/Host Bridge | 单题模式 | 过度工程，后续多题再加 |
| 运行环境 | 宿主机 | Codex 需完整系统权限和网络访问 |
| 实时注入 | PostToolUse hook | 借鉴 CHYing 读后清空机制，解决 Codex 单轮内看不到 Hermes 指导的问题 |
| hook 配置 | 全局 ~/.codex/hooks.json | 工作目录不固定 (每次挑战不同)，全局配置 + hook 脚本从 stdin cwd 找文件 |
| flag 检测 | progress.md Flags Found 段 | 不猜 flag 格式 (前缀/内容/模板不确定)，靠 Codex 主动声明 |
| Ctrl+C | SIGINT/SIGTERM trap + INTERRUPT 标志 | 用户中断不续跑，只有 Codex 自然退出才续跑 |
| 题型策略拆分 | strategies/ 目录，AGENTS.md 只保留通用原则 | 三种题型流程差异大，全塞 AGENTS.md 浪费上下文，按需读取更合理 |
| 题型支持 | web/crypto/misc 三种 | CTF 不只有 web 题，crypto/misc 有本地附件，run.sh 按类型区分 prompt 和附件处理 |
| 监控面板 | dashboard.py (stdlib HTTP + SSE) | 命令行只看到 Hermes 输出，需要同时看 Codex 日志；零依赖，和项目风格一致 |
