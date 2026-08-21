# CTF Agent

Claude (deepseek) 解题 + Hermes 监督 + Subagent 并行试探的 CTF 自动解题系统。
支持多题调度、时间片轮转、跨轮次 session 恢复、flag 全量收集与人工审查。

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│        Master 多题调度 (master/master.py)                          │
│  adapter: live(面板)/tsec(托管)/none/mock → 题目列表 + flag 提交    │
│  backend: docker(本地容器)/process(托管子进程)/fake(测试)           │
│  状态机/优先级/时间片轮转/超时预算 | 面板 :8081                     │
│  跨轮次: .cc_session/.hermes_session → 下轮 --resume 原会话         │
│  flag 全量收集: work_dir 全文件 → flag_candidates.jsonl → Hermes 审查│
└──────────────┬───────────────────────────────────────────────────┘
               │ docker run (本地) 或 bash run.sh (托管/本地 process)
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Solver (ctf-solver 容器 或 run.sh 子进程)                         │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │      Hermes (监督者/外接大脑)                                │  │
│  │  monitor.py 增量读 codex.log → hermes agent (flash)         │  │
│  │  写 guidance.md / dead_ends.md / board.md                   │  │
│  │  审查 flag_candidates → 命令补写 / 标记 rejected             │  │
│  └──────────────────┬─────────────────────────────────────────┘  │
│                     │ md 文档 + PostToolUse hook                  │
│                     ▼                                             │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │      Claude Code (主解题者, deepseek-v4-pro)                │  │
│  │  --resume 原会话续跑 | 按题型 prompt ≤10 轮                 │  │
│  │  guidance/dead_ends hook 实时注入(读后清空)                 │  │
│  └──────────────────┬─────────────────────────────────────────┘  │
│                     │ branch.py (daemon, 异步)                    │
│                     ▼                                             │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │      Subagents (试探者, branch.py daemon 管理)               │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

四个角色：
- **Master 调度器** -- 从平台拉题、调度 solver、时间片轮转（每圈换题保留断点）、回收 flag 提交、状态持久化
- **Claude (cc)** -- 主解题者，容器/子进程内唯一决策者，负责侦察、分析、决策、利用全流程
- **Hermes** -- 监督者/外接大脑，持续读 codex.log 理解进度，给建议(guidance)/下死命令(dead_ends)/维护看板(board.md)
- **Subagent** -- 试探者，branch.py daemon 异步管理，并行试探分岔路口

支持的题目类型：Web（靶场 URL）、Crypto（本地附件）、Misc（本地附件）、Binary（远程服务/制品）。
多 flag 题（flag_count>1）：拿满才走，时间片预算 = base × flag数 × 0.7。

## 运行模式（重要）

**本地模式 = 面板驱动**：solver 放 Docker 容器（DockerBackend + ctf-solver 镜像）。
```bash
cd ~/ctf-agent
python3 master/master.py --config master/master_config.json
# 浏览器开 http://localhost:8081 → 「平台接入」输入 token → 热切换拉题
```
启动时不需要环境变量 token；接入信息通过面板 `connect_platform` API 热切换。

**托管模式 = 免面板**：平台注入 BENCHMARK_BASE_URL/BENCHMARK_TOKEN/DEEPSEEK_API_KEY，
entrypoint 直接连，solver 为容器内 run.sh 子进程（backend=process）。
```bash
# 构建托管镜像（自动先建本地基础镜像）
bash docker/hosted/build-hosted.sh   # → ctf-solver-hosted:latest + tar.gz
```

**测试模式**：`backend: fake`（不起真实 agent）+ `adapter: mock`（内置假题），秒级跑调度逻辑。

## 核心机制

### 时间片轮转 + 跨轮次 session 恢复
- 每道题每圈一个时间预算：`round_time_base + (圈号-1)×round_time_step`，多 flag 乘 0.7
- 超时 → 换题（保留断点）→ 下圈带 `--resume <sid>` 续跑**原 claude 会话**（非读 board 降级）
- 关键文件：work_dir/.cc_session（claude）、.hermes_session（hermes），master 轮转时读取存 cc_session_id
- run.sh 进程替换 `>(grep --line-buffered ...)` 过滤 thinking_tokens + 实时落盘 codex.log

### flag 全量收集（不直接提交）
- master 扫描 work_dir 全部文本文件（board/codex.log/产物）提取 flag{...} 候选
- 写 `flag_candidates.jsonl`（含来源，pending）→ monitor 触发 Hermes 审查
- Hermes 读来源确认：真 flag → dead_ends.md 命令解题者补写 progress.md；噪音 → 标 rejected
- 补写后 master 走正常 `_read_flags` 提交（不信任 agent 一定写 progress.md）

### 多 flag 长跑
- flag_count>1 的题"拿满再走"，每圈完整预算、到点轮转、下圈 resume 续攻
- `_round_timeout` 用 `started_round`（分发时圈号）算预算，避免跨圈漂移放大占槽

## 项目结构

```
~/ctf-agent/
├── master/                   # 多题调度
│   ├── master.py             # 调度主循环 + Config + 圈推进/轮转
│   ├── solver_pool.py        # solver 后端 (process/docker/fake) + stop 杀进程组
│   ├── challenge_state.py    # 挑战状态机 + extract_flags_all
│   ├── prioritizer.py        # 选题优先级 (0.7×解题率 + 0.3×分值)
│   ├── submitter.py          # flag 提交
│   ├── cred_snapshot.py      # 凭据快照 (docker 挂载, 含 hooks.json 重写)
│   ├── master_dashboard.py/.html  # 面板 (:8081) + 平台接入 API
│   ├── adapters/             # none/mock/tsec/live 平台适配
│   └── master_config*.json   # 场景配置: .json(本地面板) .hosted(托管) .demo/.smoke/.tsec
├── solver/                   # 单题 Solver (容器内或子进程)
│   ├── run.sh                # 启动脚本 + claude 后台调用 + cleanup 提取 session
│   ├── AGENTS.md / TOOLS.md  # 解题指令 / 工具手册
│   ├── monitor.py            # Hermes 的眼睛 (增量读 codex.log + flag 候选触发)
│   ├── hermes_monitor.md     # Hermes 监督 prompt
│   ├── branch.py             # Subagent daemon + CLI
│   ├── dashboard.py/.html    # 单题面板
│   └── hooks/                # PostToolUse hook (guidance/dead_ends 注入)
├── docker/
│   ├── solver/               # ctf-solver 镜像 (本地): Dockerfile / build.sh
│   └── hosted/               # ctf-solver-hosted (托管): Dockerfile.hosted / build-hosted.sh / entrypoint
├── challenges/               # 每道题的 work_dir (manual_web_<hash>/, 自动创建)
├── cred_snapshots/           # 凭据快照 (敏感, gitignore)
├── master_logs/              # run.sh stdout 收集 (master 分发时写)
├── att/                      # 附件缓存
├── docs/                     # 设计文档 + 比赛日志分析
├── scripts/                  # switch-api.sh 等辅助脚本
└── tests/                    # 测试 (见下)
```

## 测试

全部在 `tests/`，无需真实 API（除标注外）：
```bash
python3 tests/test_master.py              # 调度器回归 9/9 (fake 后端)
python3 tests/test_rotation.py            # 轮转/圈推进 103/103
python3 tests/test_round_resume.py        # 跨轮次 session 机制 22/22 (含 SIGINT cleanup)
python3 tests/test_flag_collection.py     # flag 收集 36/36
python3 tests/test_flag_collection_edges.py # 边界: 去重/seen/损坏/rejected 18/18
python3 tests/test_flag_collection_e2e.py # 收集→审查→补写→提交 闭环
python3 tests/test_session_e2e_real.py    # 真实 claude/hermes resume (花 API 费)
python3 tests/test_sim_live.py            # 真实 agent 仿真 (花 API 费, 无解题)
```
注意：`test_rotation.py` 单独跑耗时约 5-8 分钟（场景多）。

## 关键参数 (master_config.json)

| 参数 | 默认 | 说明 |
|------|------|------|
| adapter | live | none=手动题 / mock=假题 / tsec=腾讯 / live=面板接入 |
| backend | docker | process=子进程(托管) / docker=容器(本地) / fake=测试 |
| max_solvers | 3 | 并行 solver 数 |
| max_challenges | 100 | 尝试题数上限 |
| round_time_base | 1200 | 第 1 圈每题秒数 |
| round_time_step | 600 | 每圈递增秒数 |
| max_rounds | 5 | 最多轮转圈数 |
| resident | true | 面板模式常驻等接入 |
| solver_timeout | 3600 | 无题可换时单 solver 兜底时长 |

## 部署与镜像

```bash
# 本地镜像 (ctf-solver:latest)
bash docker/solver/build.sh

# 托管镜像 (ctf-solver-hosted:latest + tar.gz, 自动先建本地基础镜像)
bash docker/hosted/build-hosted.sh

# 手动跑一道题 (认证/题目用挂载注入)
# 见 docker/solver/build.sh 顶部注释
```

托管部署：上传 ctf-solver-hosted.tar.gz + 平台注入 BENCHMARK_TOKEN / DEEPSEEK_API_KEY。

## 历史要点

- 2026-08-21 修复跨轮次 session 恢复：run.sh 前台管道在 SIGINT 时被 claude 卡住 → 改后台+进程替换；master stop SIGINT 后 SIGKILL claude → cleanup 提取 session
- 2026-08-21 修复 started_round 预算漂移：长跑题跨圈预算被 current_round 放大
- 2026-08-21 flag 全量收集：不直接提交 → Hermes 审查 → dead_ends 命令补写
