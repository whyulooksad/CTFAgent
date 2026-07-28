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
│  侦察 -> 分析 -> 决策 -> 利用，自动续跑最多10轮   │
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
├── AGENTS.md                 # Codex 系统指令
├── run.sh                    # 启动脚本
├── branch.py                 # Subagent daemon + CLI
├── monitor.py                # Hermes 的眼睛 (tail codex.log)
├── hermes_monitor.md         # Hermes 监控 agent 的 prompt 指令
├── hooks/
│   └── check_guidance.py     # PostToolUse hook 脚本
├── ctf-agent-design.md       # 详细设计文档
└── challenges/
    └── manual_<host>_<port>/ # 每次挑战的工作目录 (自动创建)
        ├── progress.md       # Codex 写: 轻量状态
        ├── board.md          # Hermes 维护: 结构化看板
        ├── guidance.md       # Hermes 写: 软建议 (hook 注入后清空)
        ├── dead_ends.md      # Hermes 写: 硬约束 (hook 注入后清空)
        ├── codex.log         # Codex 运行日志
        ├── branch.sock       # daemon socket (运行时)
        ├── branch_state.json # daemon 状态持久化
        └── branch_result_*.md # subagent 结果
```

## 启动

```bash
cd ~/ctf-agent
./run.sh "<target_url>" "<background_hint>"
```

示例：
```bash
./run.sh "http://target:8080" "这是XX系统，可能存在SQL注入"
```

run.sh 会自动完成：
1. 创建挑战工作目录 `challenges/manual_<host>_<port>/`，初始化 progress.md / board.md / guidance.md / dead_ends.md
2. 启动 branch.py daemon（subagent 管理进程）
3. 启动 Hermes 监控循环（每 10s 跑 monitor.py，有新日志时调 hermes agent）
4. 启动 Codex 解题（自动续跑最多 10 轮，每轮检查 progress.md 的 Flags Found 段）
5. 找到 flag 或达到最大轮次后退出，自动清理 daemon 和监控循环

运行过程中可以随时查看进度：
```bash
# 看 Codex 当前状态
cat challenges/manual_<host>_<port>/progress.md

# 看 Hermes 维护的看板
cat challenges/manual_<host>_<port>/board.md

# 看 Codex 实时日志
tail -f challenges/manual_<host>_<port>/codex.log

# 看 subagent 状态
python3 branch.py status --work-dir challenges/manual_<host>_<port>/
```

## 停止

**正常停止**：找到 flag 或跑完 10 轮后自动退出，trap 会清理 daemon 和监控循环。

**手动停止**：`Ctrl+C`。run.sh 通过 SIGINT/SIGTERM trap 捕获中断信号，设置 INTERRUPTED 标志，当前 Codex 轮次结束后不再续跑，cleanup 函数清理所有子进程。

**强制清理**（如果异常残留）：
```bash
# 找到残留进程
ps aux | grep -E 'branch.py|monitor.py|codex' | grep -v grep

# kill 掉
kill <pid>

# 清理 socket
rm challenges/manual_<host>_<port>/branch.sock
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

可通过环境变量 `CODEX_CMD` 覆盖 codex 命令路径（默认 `codex`）。
