# CTF Agent

Codex 解题 + Hermes 监督 + Subagent 并行试探的 CTF 自动解题系统。

## 架构

```
┌───────────────────────────────────────────────┐
│              Hermes (监督者/外接大脑)            │
│  monitor.py 每 10s tail codex.log，有新日志     │
│  时调 hermes agent，agent 看日志增量自己判断     │
│  介入方式: 写 guidance.md / dead_ends.md        │
│  辅助维护: board.md (供 Codex compact 后恢复)    │
└──────────────────┬────────────────────────────┘
                   │ md 文档 + PostToolUse hook
                   ▼
┌───────────────────────────────────────────────┐
│              Codex (主决策者/解题者)             │
│  模型: GPT5.6 | reasoning: medium              │
│  按题型读 strategies/<type>.md，自动续跑最多10轮  │
│  guidance/dead_ends 通过 hook 实时注入(读后清空)  │
└──────────────────┬────────────────────────────┘
                   │ branch.py (daemon, 异步)
                   ▼
┌───────────────────────────────────────────────┐
│           Codex Subagents (试探者)              │
│  branch.py daemon 长驻管理 (unix socket)       │
│  遇到分岔路口并行 spawn，结果写文件回收          │
└───────────────────────────────────────────────┘
```

三个角色：
- **Codex 主进程** -- 唯一决策者，负责侦察、分析、决策、利用全流程
- **Hermes** -- 监督者/外接大脑，持续看日志理解 Codex 状态，主动给建议(guidance)和下死命令(dead_ends)
- **Codex Subagent** -- 试探者，branch.py daemon 异步管理，并行试探分岔路口

支持的题目类型：Web（靶场 URL）、Crypto（本地附件）、Misc（本地附件）。

## 前置依赖

1. **Codex CLI** -- `npm install -g @openai/codex`，需要已登录 (gpt-5.6-sol)
2. **Hermes Agent** -- 已安装，用于后台监控 (`hermes chat -q`)
3. **Python 3** -- 标准库即可，无第三方依赖
4. **CTF 工具** -- curl/nmap/ffuf/sqlmap 等按需安装

## 部署配置

### 1. Codex 全局配置 `~/.codex/config.toml`

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "medium"

[features]
guardian_approval = false
```

### 2. Codex PostToolUse hook `~/.codex/hooks.json`

hook 配在全局是因为工作目录不固定（每次挑战一个子目录）。hook 脚本从 stdin 的 JSON 里读 `cwd` 字段，据此找到对应工作目录下的文件。

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /home/stw/ctf-agent/hooks/check_guidance.py",
            "timeout": 5,
            "statusMessage": "检查监督者指导"
          }
        ]
      }
    ]
  }
}
```

hook 机制：每次 Codex 执行完 Bash 命令后，检查工作目录下的 `guidance.md` 和 `dead_ends.md`，有内容则通过 additionalContext 注入给 Codex，然后清空文件（读后清空）。无内容时静默退出，不占上下文。

### 3. 项目文件结构

```
~/ctf-agent/
├── AGENTS.md                 # Codex 系统指令 (通用)
├── run.sh                    # 启动脚本 (支持 web/crypto/misc 三种题型)
├── branch.py                 # Subagent daemon + CLI
├── monitor.py                # Hermes 的眼睛 (tail codex.log)
├── hermes_monitor.md         # Hermes 监控 agent 的 prompt 指令
├── dashboard.py              # Web 面板后端 (HTTP + SSE)
├── dashboard.html            # Web 面板前端
├── hooks/
│   └── check_guidance.py     # PostToolUse hook 脚本
├── strategies/
│   ├── web.md                # Web 题攻击流程
│   ├── crypto.md             # Crypto 题攻击流程
│   └── misc.md               # Misc 题攻击流程
├── ctf-agent-design.md       # 详细设计文档
└── challenges/
    └── manual_<name>/        # 每次挑战的工作目录 (自动创建)
        ├── progress.md       # Codex 写: 轻量状态
        ├── board.md          # Hermes 维护: 结构化看板
        ├── guidance.md       # Hermes 写: 软建议 (hook 注入后清空)
        ├── dead_ends.md      # Hermes 写: 硬约束 (hook 注入后清空)
        ├── codex.log         # Codex 运行日志
        ├── hermes.log        # Hermes 监控日志
        ├── branch.sock       # daemon socket (运行时)
        ├── branch_state.json # daemon 状态持久化
        └── branch_result_*.md # subagent 结果
```

## 启动

### 方式一：Web 面板（推荐）

```bash
cd ~/ctf-agent
python3 dashboard.py
```

浏览器打开 `http://localhost:8080`，在页面上：
1. 选择题目类型（Web / Crypto / Misc）
2. Web 填靶场 URL，Crypto/Misc 填本地附件路径
3. 填写题目背景信息
4. 点击「启动」

页面会实时展示：
- **左侧** Codex 实时日志（codex.log 增量推送）
- **右侧** Hermes 思考与决策（hermes.log 增量推送）
- **底部状态栏** 当前 Phase / Round / Flags / Subagents

![](https://fastly.jsdelivr.net/gh/whyulooksad/image_bed@main/images/20260729212058522.png)

### 方式二：命令行

```bash
cd ~/ctf-agent

# Web 题
./run.sh --type web --url "http://target:8080" --hint "这是XX系统，可能存在SQL注入"

# Crypto 题
./run.sh --type crypto --attachment "/path/to/challenge.zip" --hint "RSA，给了公钥和密文"

# Misc 题
./run.sh --type misc --attachment "/path/to/file.zip" --hint "图片隐写"
```

run.sh 会自动完成：
1. 创建挑战工作目录，初始化 progress.md / board.md / guidance.md / dead_ends.md
2. Crypto/Misc 题会自动复制附件到工作目录
3. 启动 branch.py daemon（subagent 管理进程）
4. 启动 Hermes 监控循环（每 10s 跑 monitor.py，有新日志时调 hermes agent，输出写 hermes.log）
5. 启动 Codex 解题（按题型读 strategies/<type>.md，自动续跑最多 10 轮）
6. 找到 flag 或达到最大轮次后退出，自动清理 daemon 和监控循环

运行过程中可以随时查看进度：
```bash
# 看 Codex 当前状态
cat challenges/manual_<name>/progress.md

# 看 Hermes 维护的看板
cat challenges/manual_<name>/board.md

# 看 Codex 实时日志
tail -f challenges/manual_<name>/codex.log

# 看 Hermes 监控日志
tail -f challenges/manual_<name>/hermes.log

# 看 subagent 状态
python3 branch.py status --work-dir challenges/manual_<name>/
```

## 停止

**Web 面板**：点击「停止」按钮。

**命令行**：`Ctrl+C`。run.sh 通过 SIGINT/SIGTERM trap 捕获中断信号，设置 INTERRUPTED 标志，当前 Codex 轮次结束后不再续跑，cleanup 函数清理所有子进程。

**正常退出**：找到 flag 或跑完 10 轮后自动退出，trap 会清理 daemon 和监控循环。

**强制清理**（如果异常残留）：
```bash
# 找到残留进程
ps aux | grep -E 'branch.py|monitor.py|codex|dashboard' | grep -v grep

# kill 掉
kill <pid>

# 清理 socket
rm challenges/manual_<name>/branch.sock
```

## 关键参数

| 参数 | 值 | 说明 |
|------|------|------|
| MAX_RETRIES | 10 | Codex 自动续跑最多 10 轮 |
| TIMEOUT_SECONDS | 7200 | 整体超时 2 小时 |
| STALE_LOG_SECONDS | 300 | 日志无更新 >5 分钟触发 stale 信号 |
| DEFAULT_TIMEOUT (subagent) | 300 | 单个 subagent 默认 5 分钟 |
| 监控轮询间隔 | 10s | monitor.py 每 10 秒执行一次 |
| model_reasoning_effort | medium | Codex 推理程度 |
| MAX_LOG_LINES | 80 | monitor.py 单次输出最大日志行数 |
| Dashboard 端口 | 8080 | `python3 dashboard.py --port <port>` 可改 |

可通过环境变量 `CODEX_CMD` 覆盖 codex 命令路径（默认 `codex`）。
