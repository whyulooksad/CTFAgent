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
├── TOOLS.md                         # 环境工具手册 (main 引入, Codex 按需读取; 含二进制题流程)
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
│       └── SOLVER_SYNC.md            # main→master-agent 的 solver 同步记录
│       └── hermes-src/              # (构建时从 ~/.hermes 同步, gitignore)
│
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
5. CTF 工具按需（进程后端用宿主机的；容器镜像已装齐 nmap/sqlmap/ffuf/gobuster/dirsearch/wfuzz/binwalk/steghide/exiftool/dirb 字典/tshark/foremost/gdb/pwntools/z3/pycryptodome，手册见 `TOOLS.md`）

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

### 1. TSec 平台真机跑分（腾讯 tsecbench）

```bash
# 前置: 平台 VPN 已连 (预检点 http://10.0.100.58 返回 status:ok)
TSEC_TOKEN="<平台任务token>" \
TSEC_EXCLUDE_PREFIXES="b" \
  python3 master/master.py --config master/master_config.tsec.json
```

- `TSEC_TOKEN`：平台创建跑分任务时返回的 UUID（请求头 `BENCHMARK_TOKEN`，非网站登录 token；有效期短，跑前现取）。
  **状态/flag 文件按 token 隔离**：自动存为 `master_state_tsec_<token前8位>.json` /
  `flags_tsec_<token前8位>.jsonl`，历史保留不删——新 token 全新进度，同 token 重启正确恢复，
  不同轮次的 `submitted_correct` 不会互相污染
- `TSEC_EXCLUDE_PREFIXES`：排除不做的题系（逗号分隔题号前缀，如 `"b,f2"`；默认空）
- 平台 63 题 6 大维度：`a`=web 挖掘、`b`=多阶段渗透(多flag)、`c`=面板渗透、`d`=云、
  `e1/e2/e3`=对抗规避——均按 **web** 流程调度；`f1/f2`=二进制——按 **binary** 流程
  （TOOLS.md 二进制章节 + 镜像内 pwntools/gdb/binutils/z3 工具链）
- 活跃靶机上限 3（与 `max_solvers=3` 对齐），解出自动提交，通关自动 close 释放名额
- 多 flag 题（b 系 4-6 个）：**同一 solver 持续攻坚**——容器内声明 `--flag-count N`，
  codex 拿满全部 flag 才退出；未通关期间不关容器；面板显示 `⚑×N 已得M`；
  平台 duplicate 响应不计分不重试（防死循环）
- 停止收尾（释放平台靶机）：
  ```bash
  pkill -f master_config.tsec; sleep 5
  docker ps --format '{{.Names}}' | grep solver | xargs -I{} docker rm -f {}
  # 再对仍 available 的题逐个 POST /openapi/v1/challenges/close?unique_code=<code>
  ```

### 2. 手动调试/演示（由廉价到昂贵）

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

### 3. 单题直跑（不走 Master）

```bash
# Web 题命令行
bash solver/run.sh --type web --url "http://target:8080" --hint "背景信息"
# Crypto/Misc
bash solver/run.sh --type crypto --attachment "/path/to/file.zip" --hint "RSA"
# Binary (远程服务 + 可选制品附件)
bash solver/run.sh --type binary --url "http://target:9999" [--attachment ./pwn.bin] --hint "栈溢出"
# 多 flag 题声明总数 (拿满才退出)
bash solver/run.sh --type web --url ... --flag-count 4
# 或单题面板
python3 solver/dashboard.py    # :8080
```

### 4. 测试

```bash
python3 tests/test_master.py   # 7 项: flag提取/排序/LLM回退/面板API/手动+常驻/多flag防死循环/端到端
```

## Docker 构建

```bash
bash docker/solver/build.sh          # 同步 ~/.hermes 源码 + 构建镜像 (默认国内源)
docker/solver/build.sh --no-sync     # 跳过 hermes 同步
```

- 镜像内 codex 锁定 0.147.0、hermes 以 editable 方式装进 Linux venv（macOS 的 venv
  二进制不能拷贝）、项目以 git 仓库形态烘焙进 `/opt/ctf-agent`
- 工具链含二进制题所需：binutils / gdb / ltrace / strace / socat / pwntools
- **架构跟随构建机**：Apple Silicon 构建 = arm64；**WSL x86 机器上需重新跑 build.sh** 得到
  amd64 镜像
- TUNA pypi 偶发个别 wheel 403 时换阿里云源：
  `PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ bash docker/solver/build.sh`

### 凭据快照与容器内 hooks 机制（cred_snapshot.py）

Master 用 docker 后端时自动生成（也可手动 `python3 master/cred_snapshot.py`），
产物挂载为容器的 `/root/.codex` 和 `/root/.hermes`。**codex 部分做的是精制拷贝**：

| 文件 | 容器内处理 | 目的 |
|---|---|---|
| `auth.json` / `models_cache.json` | 原样复制 | 登录态与模型目录 |
| `ctf.config.toml` | 复制 + 两处路径重写：`model_catalog_json` → `/root/.codex/...`；hook 命令 → 绝对路径 `python3 /opt/ctf-agent/solver/hooks/check_guidance.py` | profile 机制保留；hook 不依赖运行时 git 布局 |
| 主 `config.toml` | **不复制，重新生成最小版**（仅 model/effort/项目 trust） | 剔除宿主 desktop 专属配置（notify/marketplaces/mcp_servers/projects，其中 node_repl MCP 指向 /Applications 会卡启动 120s） |
| `~/.hermes/skills/` | 整目录复制 | run.sh 的 `-s ctf-supervisor-knowledge` 在容器内可用 |

**与宿主机 desktop 的隔离是双保险**：① 机制层——hook 只在 `--profile ctf` 加载
（run.sh 专用），不带该 flag 的 codex 进程（含 desktop）永远不触发；② 物理层——
容器挂的是**快照副本**（复制而非挂载宿主目录），容器内 codex 的任何写入（含 auth
刷新）都只落在快照里，宿主 `~/.codex` 不会被触碰。

**WSL 直接可用**，前置仅三项：WSL 上装好 codex-cli 并 `codex login`；按上文在 WSL 的
`~/.codex/ctf.config.toml` 放同样的 profile（hook 命令两种路径形态——`hooks/` 或
`solver/hooks/`——快照都认）；确认 `~/.codex/models_cache.json` 存在（codex 跑过一次
即有）。快照生成对路径做了 host 无关处理，macOS/WSL 通用。

## 网络环境注意事项（重要，踩过的坑）

- **codex 出网走宿主代理**：DockerBackend 自动探测宿主代理端口（macOS scutil + 常见
  端口），容器注入 `HTTP(S)_PROXY=http://host.docker.internal:<port>`；可用环境变量
  `PROXY_FOR_CONTAINERS` 显式指定
- **Docker Desktop「系统代理」是大坑**：它会在虚拟机网络层透明劫持容器 80 端口流量
  送进 Clash；Clash 若把内网段（如 `10.0.160.x`）送去公网节点 → 靶机全变 502 假响应
  （题本身是好的）。两种解法任选：① Docker Desktop 设置代理为 manual 且清空；
  ② Clash 加规则 `IP-CIDR,10.0.0.0/8,DIRECT`
- **VPN 靶机直连**：容器注入的 `NO_PROXY` 已含私网段；靶机访问不走代理

## Master 配置项（master_config.json）

| 键 | 默认 | 说明 |
|------|------|------|
| adapter | none | 平台适配器: none(手动+面板接入) / mock / tsec(腾讯跑分) / live(通用) |
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

## main → master-agent 的 solver 合并流程

`main` 分支是单个 solver 的持续优化线（独立提交平台测试）；`master-agent` 把 solver
打进镜像作为调度消耗品。**合并 = 把 main 的解题核心成品替换进本分支**（不是 git 分支
合并——两边目录结构已分叉），流程固定如下：

1. **同步清单**（main 平铺 → 本分支位置，只拷解题核心）：

   | main 的文件 | 放到 | 方式 |
   |---|---|---|
   | branch.py / monitor.py / hermes_monitor.md / dashboard.py / dashboard.html | solver/ | 直接覆盖（main 独有优化） |
   | run.sh / AGENTS.md | solver/ | **手工合并**（见第 2 步） |
   | TOOLS.md | **仓库根** | 直接覆盖（AGENTS.md 引用 `../../TOOLS.md`） |

   不同步：main 的 challenges/ 运行产物、README、设计文档。

2. **重放 master 适配**（solver 文件里 master 分支埋的 8 处改动，丢了就出事故）：
   - `run.sh`：`--flag-count N`（多 flag 拿满才退出）+ `--type binary` +
     Flags Found 段噪音过滤（防假闭环）+ `grep -P`→POSIX sed（macOS）+
     `REPO_ROOT` 路径（work_dir 在仓库根 challenges/）+ branch socket 短路径查询
   - `AGENTS.md`：Flags Found 段只写 flag 本身的约束
   - `dashboard.py`：`CHALLENGES_DIR` 指向仓库根
   - 若 main 又加了新的 hermes skill 调用：确认 skill 已装宿主机（`hermes skills list`），
     cred_snapshot 会把 `~/.hermes/skills/` 带进容器

3. **检查环境差异**：main 的 TOOLS.md/AGENTS.md 若引用新工具 → 更新
   `docker/solver/Dockerfile`（Debian 源没有的包不能加，如 seclists 是 Kali 专属）

4. **验证**：`python3 tests/test_master.py`（8/8）→ `master_config.smoke.json`
   进程后端 mock 一轮（3/3）→ 需要容器时重建镜像跑 docker 冒烟

5. **记录**：在 `docker/solver/SOLVER_SYNC.md` 追加一行（main commit 哈希 +
   重放清单），镜像 tag 用 `ctf-solver:<日期>-<main短哈希>` 便于回滚

## 设计文档

- `docs/ctf-agent-design.md` -- Solver 层（Codex/Hermes/Subagent 协作、文件协议、看板）
- `docs/master-agent-spec.md` -- Master 层完整规格：状态机、重试规则、Docker 化方案、
  凭据精制快照、四阶段开发记录与决策表

## 待办

- [x] ~~Phase 4: 真实 API 对接~~ —— TSec 平台已接入并真机验证（2026-08-16，单轮 9 题
      解出提交：a 系列 web + c 系列面板，b 系列多 flag 与 f 系列 binary 各验证 1 题）
- [ ] WSL 上重建 amd64 镜像并复跑 docker 冒烟（比赛机迁移）
- [ ] hermes 可能需要更多 CTF 做题技巧，且人应该可以和 Hermes 交互
- [ ] hermes 监控输出偶尔超限（ARK API max_token），考虑更大 token 模型或压缩管理
- [ ] board.md 容量管理（8 ideas + 12 memory 偏小，改大还是做压缩）
- [ ] branch daemon 健壮性：崩溃后 socket 残留；结果文件完全依赖模型自觉写，
      应由 daemon 在超时/被杀时自动写终态模板
- [ ] f 系（二进制）真机仅验证 1 题，f2 固件类未实战检验

## 已解决的实战问题（防复发备忘）

| 现象 | 根因 | 修复 |
|---|---|---|
| 靶机全 502、solver 空转 | Docker Desktop 系统代理劫持容器流量送 Clash，内网段被送公网节点 | Docker 代理改 manual 清空 / Clash 加 `10.0.0.0/8,DIRECT` |
| b 系列多 flag 反复"解出"同一 flag | duplicate 被当 correct + flags_seen 被清空 → 死循环 | duplicate 不计分不回收；flags_seen 保留；同 solver 持续攻坚 |
| recon 阶段 solver 被误杀 | Codex 把进度笔记写进 Flags Found 段被当 flag | 提取端"像 flag"过滤 + AGENTS.md 约束 + run.sh 同款过滤 |
| 手动题失败被莫名重试 | 0分0解被 rarity 公式判"高价值" | 手动题不自动重试；web 重试前靶机探活 |
| 腾讯侧 3 容器、master 面板只有 2 个在跑，其余"靶机不可达" | 平台 start 返回地址时容器仍在启动，5s 预检误判不可达 → 终态+close 刚开的容器 → close 超时泄漏平台槽位 → 后续 start 撞 409 | 平台题预检失败改冷却等待（约 4 分钟窗口，容器就绪后复用同地址）；close 失败退避重试 3 次 |
| 新 token 启动带着旧 token 的 submitted_correct / 重试计数 | 状态文件跨 token 复用 | 状态/flag 文件按 token 前 8 位隔离，历史保留 |
| hermes 会话复用失效 | run.sh 用了 BSD grep 不支持的 `grep -P` | 改 POSIX sed 提取 session_id |
