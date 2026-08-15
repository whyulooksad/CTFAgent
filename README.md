# CTFAgent

Codex 解题 + Hermes 监督 + Subagent 并行试探的 CTF 自动解题系统，外加 **Master 多题并行调度层**：比赛时从赛方 API 拉题、分发到多个 Docker 隔离的 Solver 并行解题、自动提交 flag。

```
┌──────────────────────────────────────────────────────────────┐
│                     Master (调度层, master/)                   │
│  拉题 → 优先级排序(规则+LLM) → 分发 → 监控 → 自动提交(频控)      │
│  总览面板 :8081 | 失败高价值题重试 | 达题量上限自动收尾            │
└──────────────┬───────────────────────────────────────────────┘
               │ 每题一个实例 (进程 / Docker 容器, 销毁重建)
               ▼
┌──────────────────────────────────────────────────────────────┐
│              Solver × N (解题层, solver/, 容器内完整复刻)         │
│  Codex (解题) ← guidance/dead_ends 注入 ← Hermes (监督)          │
│  branch daemon (subagent 并行试探) | 单题面板 :8080              │
└──────────────────────────────────────────────────────────────┘
通信: Master 只读各题 work_dir/progress.md 的 Flags Found 段检测 flag，
Solver 内部协议 (board/guidance/dead_ends/branch) 零改动。
```

## 目录树

```
CTFAgent/
├── README.md                        # 本文件
│
├── master/                          # ── Master 调度层 ──
│   ├── master.py                    # 调度主循环: 拉题/分发/监控/重试/收尾/崩溃恢复
│   ├── master_dashboard.py          # 总览面板后端 (HTTP+SSE, :8081)
│   ├── master_dashboard.html        # 总览面板前端 (题目卡片 + 双日志流 + 控制)
│   ├── challenge_state.py           # 题目状态机 + master_state.json 持久化 + flag 提取
│   ├── prioritizer.py               # 优先级: 规则层(分高+容易优先) + LLM 软修正
│   ├── solver_pool.py               # Solver 后端: ProcessBackend / DockerBackend / FakeBackend
│   ├── submitter.py                 # flag 自动提交: 频控 + 单题上限 + 退避重试
│   ├── cred_snapshot.py             # 容器凭据精制快照 (见 docs/master-agent-spec.md §7)
│   ├── master_config.json           # 默认配置
│   ├── master_config.smoke.json     # 手动调试: 进程后端 + mock 3 题
│   ├── master_config.docker.json    # 手动调试: Docker 后端 + mock 3 题
│   ├── master_config.demo.json      # 零成本面板演示 (fake 后端, 不起 codex)
│   └── adapters/                    # 赛方平台适配层
│       ├── base.py                  # 抽象接口 + Challenge/SubmitResult 数据结构
│       ├── mock.py                  # mock 平台: 3 道本地假题, web 题本地起靶机
│       └── live.py                  # 真实平台骨架 (Phase 4, 测试日按官方文档填充)
│
├── solver/                          # ── Solver 解题层 (单题, 原有系统) ──
│   ├── run.sh                       # 单题入口: 初始化 + daemon + Hermes 监控 + Codex 续跑
│   ├── AGENTS.md                    # Codex 系统指令
│   ├── hermes_monitor.md            # Hermes 监督 agent 指令
│   ├── monitor.py                   # Hermes 的眼睛: 10s 轮询 codex.log 增量
│   ├── branch.py                    # Subagent daemon (unix socket, spawn/kill/status)
│   ├── dashboard.py                 # 单题面板 (HTTP+SSE, :8080)
│   ├── dashboard.html               # 单题面板前端
│   └── hooks/
│       └── check_guidance.py        # PostToolUse hook: 注入 guidance/dead_ends 后清空
│
├── docker/
│   └── solver/                      # ctf-solver 镜像
│       ├── Dockerfile               # python3.11 + CTF 工具链 + codex 0.147.0 + hermes
│       ├── entrypoint.sh            # 容器入口 -> solver/run.sh
│       ├── build.sh                 # hermes 源同步 + docker build (默认国内镜像源)
│       └── hermes-src/              # (构建时从 ~/.hermes 同步, gitignore)
│
├── strategies/                      # 按题型攻击流程 (Codex 按需读取)
│   ├── web.md
│   ├── crypto.md
│   └── misc.md
│
├── tests/
│   ├── test_master.py               # 全量测试 (fake 后端, 不依赖 codex/hermes)
│   ├── fake_codex_llm.sh            # LLM 优先级测试用假 codex
│   └── mock_challenges/             # mock 假题附件 (首次运行按需生成)
│
├── docs/
│   ├── ctf-agent-design.md          # Solver 层详细设计文档
│   ├── master-agent-spec.md         # Master 层设计规格书 (含决策记录)
│   └── archive/                     # 历史调试记录归档
│
├── challenges/                      # 题目现场 (运行产物, gitignore)
│   ├── attachments/<cid>/           # Master 下载的题目附件
│   └── manual_<hash>/               # 每题工作目录 (progress/board/logs, 容器挂载点)
├── att/                             # 本地杂项附件 (gitignore)
└── .dockerignore
```

运行时还会在仓库根生成（均 gitignore）：`master_state.json`（状态机）、`master.log`、`master_logs/`（run.sh 输出）、`cred_snapshots/`（凭据快照）。

## 前置依赖

1. **Codex CLI** `npm install -g @openai/codex`（镜像内锁定 0.147.0，宿主保持同版本），已登录
2. **Hermes Agent** 已安装（`hermes chat -q` 可用）
3. **Python 3**（标准库即可，零第三方依赖）
4. Docker（仅 Docker 后端需要）
5. CTF 工具按需（进程后端用宿主机的；容器镜像已装齐 nmap/sqlmap/ffuf/gobuster/dirsearch/wfuzz/binwalk/steghide/exiftool）

## 部署配置

### Codex CLI 专用 Hook Profile `~/.codex/ctf.config.toml`

Codex Desktop 和 CLI 默认共用 `~/.codex`。为避免 CTF Hook 影响桌面版，Hook 不放在全局
`hooks.json`，而是放进仅由 `run.sh` 通过 `--profile ctf` 启用的 profile：

```toml
model_catalog_json = "/Users/<你的用户名>/.codex/models_cache.json"

[features]
hooks = true

[[hooks.PostToolUse]]
matcher = "*"

[[hooks.PostToolUse.hooks]]
type = "command"
command = 'python3 "$(git rev-parse --show-toplevel)/solver/hooks/check_guidance.py"'
timeout = 5
statusMessage = "检查 Hermes 指导"
```

profile 文件虽然位于共享的 `~/.codex`，但只有显式传入 `--profile ctf` 的 CLI 进程会加载
其中的配置；Codex Desktop 默认不会启用该 profile。`model_catalog_json` 让 CTF 主 Agent 和
Subagent 直接使用 Codex 已缓存的模型目录，避免多个 CLI 进程重复刷新模型目录。Codex
Desktop 仍可正常刷新共享的 `models_cache.json`。

hook 机制：每次 Codex 执行完受支持的本地工具后，检查工作目录下的 `guidance.md` 和
`dead_ends.md`，有内容则通过 additionalContext 注入给 Codex，然后清空文件（读后清空）。
无内容时静默退出，不占上下文。

## 启动

### 0. 日常使用（常驻模式，默认）

```bash
python3 master/master.py        # 默认配置: adapter=none + resident=true
```

- 进程**常驻**，面板 **http://localhost:8081** 永远在线（测试结束也不退出），`Ctrl+C` 停止
- **手动加题**（测其他 CTF 平台的题）：选类型（web 填靶机 URL / crypto、misc 填本地附件绝对路径）+ 描述，
  右侧「＋」加行、一次提交多题，统一进 master 调度。手动题不受题量上限约束；
  手动模式**只展示不提交**——找到 flag 即闭环，在 Flags 面板复制后自行去目标平台提交（卡片带「手动」标）
- **Flags 面板**：滚动列表展示**本次启动**解出的所有 flag（API 模式标「API 已提交」、
  手动模式标「手动」），历史归档在仓库根 `flags.jsonl`
- **平台接入**（测试日）：填赛方 API 地址 + Token，热切换 `LiveAdapter` 并立即拉题，
  顶栏徽章显示接入状态
- 调度配置区可运行时改并发数/题量上限

### 1. 手动调试/演示（由廉价到昂贵）

```bash
cd ~/workstation/cybersecurity/dsg/CTFAgent

# 零成本: fake 后端秒解 3 题纯看面板与调度流程
python3 master/master.py --config master/master_config.demo.json

# 小成本: 进程后端 + 真实 codex/hermes 解 mock 3 题 (~5 分钟)
python3 master/master.py --config master/master_config.smoke.json

# Docker 后端: 3 容器并发 (需先构建镜像)
CTF_MOCK_PUBLIC_HOST=host.docker.internal \
  python3 master/master.py --config master/master_config.docker.json
```

面板统一开 **http://localhost:8081**：题目卡片（状态/分数/尝试/时长/flag）、codex+hermes
双日志实时流、暂停/恢复调度、手动终止 solver、运行时改并发数与题量上限。

### 2. 单题直跑（不走 Master）

```bash
# Web 题命令行
bash solver/run.sh --type web --url "http://target:8080" --hint "背景信息"
# Crypto/Misc
bash solver/run.sh --type crypto --attachment "/path/to/file.zip" --hint "RSA"
# 或单题面板
python3 solver/dashboard.py    # :8080
```

### 3. 测试

```bash
python3 tests/test_master.py   # 5 项: flag提取/排序/LLM回退/面板API/端到端
```

## Docker 构建

```bash
bash docker/solver/build.sh          # 同步 ~/.hermes 源码 + 构建镜像 (默认国内源)
docker/solver/build.sh --no-sync     # 跳过 hermes 同步
```

- 镜像内 codex 锁定 0.147.0、hermes 以 editable 方式装进 Linux venv（macOS 的 venv
  二进制不能拷贝）、项目以 git 仓库形态烘焙进 `/opt/ctf-agent`
- **架构跟随构建机**：Apple Silicon 构建 = arm64；**WSL x86 机器上需重新跑 build.sh** 得到
  amd64 镜像
- 凭据快照：`python3 master/cred_snapshot.py`（Master 用 docker 后端时也会自动生成），
  做的是精制拷贝——auth 原样、`ctf.config.toml` 重写路径、主 `config.toml` 生成最小版

## Master 配置项（master_config.json）

| 键 | 默认 | 说明 |
|------|------|------|
| adapter | none | 平台适配器: none(手动+面板接入) / mock / live(测试日) |
| resident | true | 常驻模式: 不自动退出，面板永远在线 (0 = 跑完队列自动退出) |
| backend | process | solver 后端: process / docker / fake(调试) |
| max_solvers | 5 | 并行 solver 槽位数 (面板可改) |
| max_challenges | 20 | 尝试题目数上限 (去重计，重试不占) |
| solver_timeout | 3600 | 单 solver 整体超时 (秒) |
| max_retries_per_challenge | 1 | 失败重试上限，仅限高价值题 |
| retry_value_threshold / retry_rarity_threshold | 0.6 / 0.7 | 高价值判定阈值 |
| poll_interval | 15 | 主循环间隔 (秒) |
| llm_priority | true | LLM 优先级软修正开关 (codex exec, 失败回退规则层) |
| submit_min_interval / max_submit_per_challenge | 10 / 3 | 提交频控 |
| dashboard_port | 8081 | 0 = 关面板 |

## 运行产物速查

| 想看什么 | 位置 |
|---|---|
| 调度决策日志 | `master*.log`（仓库根） |
| 历次解出的 flag 归档 | `flags.jsonl`（面板 Flags 区只展示本次启动） |
| 题目状态机 | `master_state*.json` |
| 某题解题现场 | `challenges/manual_<hash>/`（progress/board/codex.log/hermes.log） |
| run.sh 输出横幅 | `master_logs/<题目id>.log` |
| 容器 stdout | `docker logs <容器名>` |
| 面板 API | `curl --noproxy '*' localhost:8081/api/overview` |

## 设计文档

- `docs/ctf-agent-design.md` -- Solver 层（Codex/Hermes/Subagent 协作、文件协议、看板）
- `docs/master-agent-spec.md` -- Master 层完整规格：状态机、重试规则、Docker 化方案、
  凭据精制快照、四阶段开发记录与决策表

## 待办

- [ ] **Phase 4**: 测试日按官方文档核对 `master/adapters/live.py` 的端点/字段/认证头
      （已有 best-effort 实现，可在面板「平台接入」直接填地址+Token 试连）
- [ ] WSL 上重建 amd64 镜像并复跑 docker 冒烟
- [ ] hermes 可能需要更多 CTF 做题技巧，且人应该可以和 Hermes 交互
- [ ] hermes 监控输出偶尔超限（ARK API max_token），考虑更大 token 模型或压缩管理
- [ ] board.md 容量管理（8 ideas + 12 memory 偏小，改大还是做压缩）
- [ ] branch daemon 健壮性：崩溃后 socket 残留；结果文件完全依赖模型自觉写，
      应由 daemon 在超时/被杀时自动写终态模板
