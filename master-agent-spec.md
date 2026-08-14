# Master Agent 设计规格书 (Specification)

> 多题并行调度层：Master 调度 + Docker Solver 池 + 平台 API 对接
>
> 状态: 待评审
> 前置文档: ctf-agent-design.md (单题 Solver 设计)

---

## 1. 背景与目标

当前系统 (以下称 **Solver**) 一次只能解一道题：`run.sh` 编排 Codex(解题) + Hermes(监督) +
branch daemon(并行试探) 的完整流程。本规格定义在其之上新增一层 **Master 调度器**，实现：

1. 通过赛方 API 拉取题目（web / crypto / misc）
2. 分发题目到多个并行 Solver 实例（Docker 容器，互相隔离，销毁重建式复用）
3. 持续监控各 Solver 状态，完成/异常时回收槽位并分发新题
4. 自动收集 flag 并提交赛方平台（带频率控制）
5. 达到人工设置的题目数量上限或题目耗尽后停止

**Master 不解题、不监督解题**。解题决策在容器内 Codex，解题监督在容器内 Hermes，
Master 只负责调度、状态机、flag 收集与提交。

### 1.1 已确认的决策记录

| 决策项 | 结论 |
|--------|------|
| Master 技术形态 | 混合: Python 确定性调度内核 + LLM (codex exec) 软决策 |
| Solver 隔离 | Docker 容器，每题一个容器，销毁重建 |
| Hermes 角色 | 每个 Solver 容器内自带 Hermes，Master 不涉及 |
| 并发数 | 默认 5，手动可配置 (`max_solvers`) |
| 优先级 | 规则 + LLM 混合: ①分高+解出多 → ②容易(解出多) → ③分高 |
| 优先级 LLM | `codex exec` 低推理档单次调用 |
| Flag 提交 | 自动提交 + 频控 |
| Solver 整体超时 | 默认 1h (`solver_timeout: 3600`)，可配 |
| 失败重试 | 最多 1 次，仅限高价值题 (分数高 / 解出人数少)，见 §4.2 |
| 错误 flag 后的 solver | 继续跑，直到超时或自然结束 |
| 靶机管理 | 有数量/时长限制，即用即开即释放 |
| 面板 | 新建 Master 总览面板 (:8081)，现有单题面板保留 |
| 开发顺序 | Phase1 先进程方式跑通，Phase2 再 Docker 化 |

### 1.2 赛方 API 假设

参考第二届腾讯智能渗透测试黑客松模式，**真实 API 测试日才公布**，故 Master 只依赖
抽象接口 (`adapters/base.py`)，真实实现 (`adapters/live.py`) 留骨架，测试日填充。
预计端点: `GET /challenges` / `POST /start_challenge` / `POST /stop_challenge` /
`POST /submit` / `GET /hint`。

---

## 2. 术语

| 术语 | 定义 |
|------|------|
| Master | 主调度器进程 (`master.py`)，本项目新增 |
| Solver | 一个完整的单题解题实例 = 现有项目全套 (Codex+Hermes+branch daemon) |
| Solver 槽位 | 一个可运行 Solver 的位置，共 `max_solvers` 个 |
| Challenge | 赛方的一道题，含 id/type/score/solve_count/url/attachment 等元数据 |
| work_dir | 单题工作目录 `challenges/<challenge_id>/`，宿主机与容器共享卷 |
| Adapter | 赛方平台 API 的适配器实现 |

---

## 3. 总体架构

```
┌────────────────────────────────────────────────────────────────┐
│                        Master (master.py)                       │
│                                                                │
│  ┌─────────────┐   ┌─────────────┐   ┌──────────────────────┐  │
│  │ Adapter 层   │   │ 优先级器     │   │ Flag 提交器           │  │
│  │ base/mock/  │   │ 规则排序 +   │   │ 队列 + 频控 + 退避     │  │
│  │ live        │   │ LLM 修正     │   │                      │  │
│  └──────┬──────┘   └──────┬──────┘   └──────────┬───────────┘  │
│         │                 │                     │              │
│  ┌──────┴─────────────────┴─────────────────────┴───────────┐  │
│  │              调度主循环 (每 15s)                           │  │
│  │  拉题→排序→分配空闲槽位→监控运行中 solver→回收→提交         │  │
│  └──────┬────────────────────────────────────────────────────┘  │
│         │ Backend 抽象 (ProcessBackend / DockerBackend)         │
│         │        docker run / docker rm -f                      │
└─────────┼────────────────────────────────────────────────────────┘
          │ 卷挂载: work_dir (rw) + 凭据快照 (ro)
          ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ solver-<cid> #1   │  │ solver-<cid> #2   │  │  ... (≤N 个)      │
│ 容器内:            │  │                  │  │                  │
│  entrypoint.sh    │  │                  │  │                  │
│   └─ run.sh 全套   │  │                  │  │                  │
│      Codex+Hermes │  │                  │  │                  │
│      +branch daemon│ │                  │  │                  │
└──────────────────┘  └──────────────────┘  └──────────────────┘
          │
          ▼
     赛方平台 (拉题 / 开关靶机 / 提交 flag)
```

**通信方式与现有项目风格一致**：Master 与 Solver 之间不走 RPC，只通过
**共享卷上的文件** 交互——Solver 写 `progress.md`（现有协议，零改动），
Master 读它检测 flag 和进度；Master 起停 Solver 通过进程/Docker API。

---

## 4. 模块设计

### 4.1 Adapter 层 (`adapters/`)

```python
# base.py 抽象接口
class PlatformAdapter(ABC):
    def list_challenges() -> list[Challenge]          # 拉题目列表(含分数/解出数)
    def start_challenge(cid) -> str                   # 开靶机，返回实例 URL (web 题)
    def stop_challenge(cid) -> None                   # 释放靶机
    def submit(cid, flag) -> SubmitResult             # 提交 flag: correct / wrong / error
    def get_hint(cid) -> str                          # 获取提示 (预留，暂不主动用)
    def download_attachment(url, dest) -> Path        # 下载附件
```

**内部 Challenge 数据结构**（与平台无关）：

```python
@dataclass
class Challenge:
    id: str                 # 平台题目 ID (唯一)
    title: str
    type: str               # web | crypto | misc
    score: int              # 当前动态分值
    solve_count: int        # 已解出人数
    url: str | None         # web 题靶机 URL (start 后才有)
    attachment_url: str | None
    description: str        # 题目描述 (供 LLM 判难度)
    attachment_path: Path | None  # Master 下载后的本地路径
```

三个实现：

| 实现 | 用途 |
|------|------|
| `adapters/mock.py` | 开发测试主力。内置 3 道本地假题(见 §9)，伪造 start/stop/submit 语义 |
| `adapters/live.py` | 真实平台骨架。端点/认证/字段映射测试日填充 |
| adapter 选择 | 配置项 `adapter: mock \| live` |

### 4.2 调度主循环 (`master.py`)

```
初始化: 加载配置 → 选 adapter → 起 dashboard
loop (每 15s):
    1. sync_challenges:  调 list_challenges() 刷新分数/解出数 (动态计分)
    2. reprioritize:     对「未分发且未尝试」的题重排 (见 §4.3)
    3. fill_slots:       while 有空闲槽位 and 队列有题 and attempted < max_challenges:
                            取队首题目 → dispatch(challenge)
    4. monitor_solvers:  对每个 running solver 检查 (见 §4.5 终止矩阵)
    5. drain_submitter:  处理提交队列结果
    6. check_stop:       attempted >= max_challenges 且无 running solver → 退出
```

**dispatch(challenge) 流程**：

```
1. 若 web 题: adapter.start_challenge(cid) 取靶机 URL (失败→放回队列尾部, 标记重试)
2. 若有附件: adapter.download_attachment() 到 challenges/<cid>/attachments/
3. backend.start_solver(challenge, work_dir)   # 起新容器/进程
4. 记录 SolverRecord, attempted += 1
```

**题目状态机**：

```
discovered → queued → dispatched → running ─┬→ flag_found → submitted_correct → closed
                                            ├→ submitted_wrong (solver 继续跑，可再 flag_found)
                                            ├→ timeout → closed (release 靶机)
                                            ├→ failed (容器异常/10轮耗尽/超时) → closed
                                            │     └→ 满足重试条件 → queued (第 2 次尝试，最多 1 次)
                                            └→ manual_stop → closed
```

**失败重试策略**（已确认决策）：失败/超时的题**最多重试一次**，且仅限"高价值难题"——
分数高或解出人数少（两者任一满足即可）：

```
value = score / max(score)          # 本轮所有题的最高分归一化
rarity = 1 - solve_count / max(solve_count)   # 解出越少越稀有
可重试 ⇔ value >= retry_value_threshold (默认 0.6) 或 rarity >= retry_rarity_threshold (默认 0.7)
```

- 容易且低分的题失败后不重试（性价比低，人力/槽位留给新题）
- 重试重新入队参与正常优先级排序；同一题第 2 次失败即终态，不再入队
- `max_challenges` 按**去重后的题目数**计，重试不重复计数；且上限只约束**新题分发**，
  已尝试题的重试不受上限限制（实现时确认的语义：给难题的第二次机会不挤占新题名额）

closed 后按难度有条件重入队一次（默认规则，见 §4.2 重试策略）。

### 4.3 优先级器 (`prioritizer.py`)

**规则层**（确定性，基础序）：

```
ease   = solve_count / max(solve_count)          # 解出人数归一化 (越多越容易)
value  = score / max(score)                      # 分数归一化
base   = 0.5 * ease + 0.5 * value                # 第一优先级: 分高+容易
排序键 = (-base, -solve_count, -score)           # 次级: 容易优先, 再次: 分高
```

**LLM 层**（软修正，可关 `llm_priority: true`）：

- 仅在「队列重新入题」时触发，输入：候选题的 title/description/type/score/solve_count
- 调用：`codex exec`（`model_reasoning_effort=low`，单次，输出 JSON 数组表示推荐顺序）
- 输出仅作顺序调整建议；解析失败/超时(30s)/非法输出 → 回退规则层结果
- 硬约束: 正在 running 的题不受重排影响；LLM 不决定"做不做"，只决定"先后"

### 4.4 Solver Backend (`solver_pool.py`)

Backend 抽象（Phase1/Phase2 只换实现，Master 逻辑不变）：

```python
class SolverBackend(ABC):
    def start(challenge, work_dir) -> SolverHandle
    def is_alive(handle) -> bool
    def stop(handle) -> None        # 优雅终止: SIGINT → 5s → SIGKILL / docker rm -f
    def logs_tail(handle, n) -> str
```

**ProcessBackend (Phase 1)**：直接 `bash run.sh --type X ...`，`os.setsid` 新进程组，
复用现有 dashboard.py 的进程管理方式。附件路径直接传本地路径。

**DockerBackend (Phase 2)**：

镜像内布局（关键约束：hook 通过 `git rev-parse --show-toplevel` 定位项目根，
项目必须以 **git 仓库形态** 进入镜像，且 work_dir 必须位于仓库树内）：

```
/opt/ctf-agent/               # 项目 git 仓库 (COPY . && git init，保留 .git 或重建)
├── run.sh / branch.py / monitor.py / AGENTS.md / strategies/ / hooks/
└── challenges/<cid>/         # work_dir 挂载点 (容器内路径，位于仓库树内)
```

```
docker run -d --name solver-<cid> \
  -v <work_dir>:/opt/ctf-agent/challenges/<cid> \   # rw, solver 的解题现场
  -v <cred_snapshot>/codex:/root/.codex \           # 精制配置快照 (见 §7.1)
  -v <cred_snapshot>/hermes:/root/.hermes \         # 精制配置快照 (见 §7.2)
  --memory 4g \
  ctf-solver:latest --type <type> --url/--attachment ... --hint ...
```

要点：
- 容器网络默认 bridge (NAT 出网即可达靶机)；不通再切 `--network host`
- `entrypoint.sh` = 现有 `run.sh` 流程，**Solver 内部代码零改动**
- 镜像内 codex CLI 版本锁定 **codex-cli 0.147.0**（`--profile ctf` 通过
  `~/.codex/<name>.config.toml` 覆盖文件实现的机制依赖该版本行为，见 §7.1）
- 镜像内必须装有 `git` + `python3`（hook 依赖）
- 销毁: `docker rm -f`，work_dir 留宿主机供复盘

### 4.5 监控与终止矩阵

Master 对每个 running Solver 的检查（复用 `monitor.py` 的解析逻辑抽出公共函数）：

| 检测项 | 手段 | 动作 |
|--------|------|------|
| flag 出现 | 解析 work_dir/progress.md 的 `## Flags Found` 段 (同 run.sh 的 awk 逻辑) | flag → 提交器；**solver 不立即销毁**，等提交结果 |
| 提交 correct | adapter.submit 返回正确 | 销毁 solver，释放槽位，web 题调 stop_challenge |
| 提交 wrong | 同上返回错误 | 记录，solver 继续跑（已确认决策）；后续新 flag 在单题提交上限内再提交 |
| 整体超时 | 运行时长 > `solver_timeout` (默认 3600s / 1h) | 销毁，标 timeout，按 §4.2 重试策略决定是否重入队 |
| 容器/进程死亡 | is_alive == false 且无 flag | 销毁记录，标 failed，按 §4.2 重试策略决定是否重入队 |
| 自然跑完无 flag | run.sh 10 轮耗尽后容器退出 | 同上标 failed |
| Master 收到停机 | 面板/信号 | 全部 stop，web 题批量 stop_challenge |

### 4.6 Flag 提交器 (`submitter.py`)

- 单线程队列，串行提交
- 频控：相邻两次提交间隔 ≥ `submit_min_interval` (默认 10s)，单题累计提交 ≤
  `max_submit_per_challenge` (默认 3)
- 平台错误 (网络/5xx)：指数退避重试 (10s/30s/60s，共 3 次)
- 结果写回题目状态并推送面板；`submitted_correct` 后槽位回收

### 4.7 Master Dashboard (`master_dashboard.py` + `.html`，:8081)

API（沿用 stdlib HTTP + SSE 风格）：

```
GET  /                       # 总览页
GET  /api/overview           # 全部题目状态 + 槽位占用 + 已得/潜在总分
GET  /api/logs/<cid>/codex   # SSE: 该题 codex.log 增量
GET  /api/logs/<cid>/hermes  # SSE: 该题 hermes.log 增量
POST /api/pause | /api/resume        # 暂停/恢复调度 (不发新题，运行中的不动)
POST /api/stop-solver        # 手动终止某 solver {cid}
POST /api/config             # 运行时改 max_solvers / max_challenges
```

总览页布局：题目卡片列表（标题/类型/分数/解出数/状态徽章/phase/运行时长/flag/
提交次数），点击展开双日志面板（复用现有 dashboard.html 的 SSE 消费代码）；
顶部: 槽位占用 x/N、attempted/max_challenges、累计得分、暂停/恢复按钮。

---

## 5. 目录结构（新增文件）

```
CTFAgent/
├── master.py                  # Master 主进程: 调度循环 + 状态机
├── master_config.json         # 配置
├── master_dashboard.py        # 总览面板后端
├── master_dashboard.html      # 总览面板前端
├── adapters/
│   ├── base.py                # 抽象接口 + Challenge 数据结构
│   ├── mock.py                # mock 平台 (内置 3 道假题)
│   └── live.py                # 真实平台骨架 (测试日填充)
├── prioritizer.py             # 规则 + LLM 优先级
├── solver_pool.py             # Backend 抽象 + Process/Docker 实现
├── submitter.py               # flag 提交 + 频控
├── challenge_state.py         # 题目/solver 状态持久化 (master_state.json)
├── docker/solver/
│   ├── Dockerfile             # solver 镜像
│   └── entrypoint.sh          # = run.sh 容器化包装
├── tests/
│   ├── mock_challenges/       # mock 假题的附件
│   └── test_master.py         # 端到端测试 (mock adapter + ProcessBackend)
└── (现有文件一律不动)
```

## 6. 配置 (`master_config.json`)

```json
{
  "adapter": "mock",
  "backend": "process",          // process | docker (Phase2 切 docker)
  "max_solvers": 5,
  "max_challenges": 20,
  "solver_timeout": 3600,
  "retry_failed": true,
  "max_retries_per_challenge": 1,
  "retry_value_threshold": 0.6,
  "retry_rarity_threshold": 0.7,
  "poll_interval": 15,
  "llm_priority": true,
  "llm_priority_effort": "low",
  "submit_min_interval": 10,
  "max_submit_per_challenge": 3,
  "dashboard_port": 8081,
  "docker_image": "ctf-solver:latest"
}
```

Master 运行状态持久化到 `master_state.json`（题目状态机 + 已提交 flag +
attempted 计数），Master 崩溃重启后可恢复（running 的 solver 标 failed 重入队或不重试，按配置）。

---

## 7. 凭据与配置快照方案（已实测检查）

检查结果（2026-08-14，本机）：

| 组件 | 认证/配置 | 多容器共用评估 |
|------|-----------|----------------|
| Codex | `~/.codex/auth.json`: ChatGPT OAuth (`id/access/refresh_token`, `last_refresh`) | **有风险**: OAuth refresh 可能轮换 refresh_token。A 容器刷新后，B 容器内旧 refresh_token 失效 |
| Codex | `ctf.config.toml`: profile 覆盖文件 (`--profile ctf` 加载，codex-cli 0.147.0 机制；主 `config.toml` 无 `[profiles]` 段) | **不能盲拷**: 含 macOS 绝对路径，见下 |
| Codex | 主 `config.toml` | **不能盲拷**: 大量桌面版专属配置，见下 |
| Hermes | `~/.hermes/auth.json`: `credential_pool` (copilot/zai) + `auth.lock` | 结构上有凭据池和锁，多实例共享快照大概率可行 |
| Hermes | 安装形态: venv 型 Python 应用 (bash wrapper → venv python，1.3GB，macOS 二进制) | **不能拷贝进容器**: 必须在镜像内为 Linux 原生安装 |

**结论：凭据快照不是 `cp -r`，而是"精制快照"——选择性拷贝 + 路径重写 + 生成最小配置。**

### 7.1 Codex 快照（Master 启动时生成，`cred_snapshots/<run_id>/codex/`）

| 文件 | 处理方式 |
|------|----------|
| `auth.json` | 原样拷贝 |
| `models_cache.json` | 原样拷贝（避免容器内重复刷新模型目录） |
| `ctf.config.toml` | 拷贝后**重写两处路径**: ① `model_catalog_json` → `/root/.codex/models_cache.json` (规则: 把宿主机 home 前缀替换为 `/root`，**host 无关**——部署宿主机是 WSL/Linux，开发机可能是 macOS，不能硬编码具体用户路径); ② hook 命令中 `$(git rev-parse --show-toplevel)/hooks/check_guidance.py` → 绝对路径 `/opt/ctf-agent/hooks/check_guidance.py`（消除对 git 仓库布局的运行时依赖，镜像布局本身仍保留 git 仓库作为双保险） |
| `config.toml` | **不拷贝原文件**，生成容器专用最小配置: `model` + `model_reasoning_effort` + `[projects."/opt/ctf-agent"] trust_level=trusted`。必须剔除: `notify`(指向 macOS app)、`[marketplaces.*]`、`[mcp_servers.node_repl]`(指向 `/Applications/ChatGPT.app`，`startup_timeout_sec=120` 会拖慢/卡住容器内启动)、`[mcp_servers.computer-use]`、`[desktop]`、`[projects.*]`(宿主机路径，容器内无意义) |

### 7.2 Hermes 快照（`cred_snapshots/<run_id>/hermes/`）

- Hermes 本体在 **Dockerfile 构建时原生安装**（Linux venv），不进快照
- 快照只含运行时状态/凭据: `auth.json`、`.env`（模型 API key 等）、`config.yaml`
  （若含 macOS 绝对路径则同样按 §7.1 方式重写，Phase 2 构建时核对）
- 镜像构建验收项：容器内 `hermes chat -q "ping"` 冒烟通过

### 7.3 OAuth refresh 风险与实测门槛

快照以**读写**挂载（codex/hermes 可能需要写 session/缓存），全部容器共享同一份。
若 codex 在容器内触发 token refresh 并轮换 refresh_token，可能引发跨容器竞争。

**Phase 2 开始时必须先做的实测**（验收门槛）：
1. 同一快照起 2+ 容器并发跑 codex exec 10 分钟，观察 token refresh 行为与 auth.json 写入
2. 若 refresh 冲突 → 降级方案 a: 每容器独立快照副本 + Master 定期从宿主机重同步;
   降级方案 b: 放弃 Docker 化，Master 保留 ProcessBackend (Phase 1 成果仍完整可用)

---

## 8. 开发阶段划分与验收标准

### Phase 1 — 调度核心（进程后端 + mock adapter）
- [ ] adapters/base.py + mock.py (3 道假题)
- [ ] challenge_state.py 状态机 + 持久化
- [ ] master.py 主循环 (sync/reprioritize/fill/monitor)
- [ ] prioritizer.py 规则层 (LLM 层接口留空)
- [ ] solver_pool.py ProcessBackend
- [ ] submitter.py
- [ ] 验收: `python3 master.py` + mock → 3 道假题全部解出并自动提交正确，
  attempted/max_challenges 生效，日志/状态文件齐全，中途 kill master 可恢复

### Phase 2 — Docker 化 (已完成，2026-08-14)
- [x] docker/solver/Dockerfile (python3.11+node+codex-cli 0.147.0+git+hermes 原生安装+CTF 工具链)
- [x] 精制配置快照生成器 cred_snapshot.py (§7.1/§7.2)
- [x] entrypoint.sh + 项目以 git 仓库形态烘焙进镜像
- [x] solver_pool.py DockerBackend
- [x] 凭据并发实测 (§7.3): 3 容器共享同一快照并发解题全部成功，无 OAuth 冲突
- [x] 验收: 容器内 codex `--profile ctf` + OAuth 冒烟通过 (exec 回复正常)；
      容器内 hermes 监督完整工作 (session 复用/board.md 维护)；3 并发容器跑 mock 题
      3/3 解出并提交正确，容器销毁零残留

Phase 2 实现时确认的细节 (与 spec 原稿的差异):
- Hermes 禁止 wheel/sdist 构建，镜像内必须 `pip install -e` (editable)
- 默认构建源走国内镜像 (TUNA apt / npmmirror / TUNA pypi)，build.sh 环境变量可覆盖；
  deb.debian.org 经 Fastly 在本网络下高延迟且偶发 502
- mock web 靶机对容器暴露需 `CTF_MOCK_PUBLIC_HOST=host.docker.internal`
  (MockAdapter 监听 0.0.0.0；真实平台靶机是公网 URL 无此问题)
- 镜像架构跟随构建机: Apple Silicon 构建 = linux/arm64，WSL x86 需在 WSL 上重新
  build.sh 构建 (docker build 自动选 amd64)
- 镜像 ENTRYPOINT 固定 exec run.sh，docker run 的 CMD 只传 run.sh 参数

### Phase 3 — 面板 + LLM 优先级
- [ ] master_dashboard.py/html 总览 + 日志 SSE + 控制
- [ ] prioritizer.py LLM 层 (codex exec)
- [ ] 验收: 面板实时反映全部 solver 状态；LLM 排序异常时回退规则层

### Phase 4 — 真实 API 对接（测试日）
- [ ] adapters/live.py 按真实文档填充
- [ ] 端到端联调
- [ ] 验收: 真实平台拉题→解题→提交闭环

---

## 9. Mock 平台设计 (`adapters/mock.py`)

内置 3 道本地假题（附件放 `tests/mock_challenges/`）：

| ID | 题目 | 类型 | 附件 | flag |
|----|------|------|------|------|
| mock-easy-misc | 签到题 | misc | zip: 文本文件 base64 编码一层 | `flag{mock_easy_misc_welcome}` |
| mock-mid-crypto | 简单异或 | crypto | zip: task.py(XOR 已知部分明文) + output.bin | `flag{mock_xor_is_easy}` |
| mock-mid-web | 本地 HTTP 隐写页 | web | 无 (mock 起本地 http.server, 注释里藏 flag) | `flag{mock_web_hidden}` |

- submit: 精确匹配即 correct；错误 3 次后仍可继续提交（mock 不惩罚）
- start_challenge: web 题拉起本地临时 http server 并返回端口；stop 时关闭
- solve_count/score 初始值设计成能验证优先级排序 (easy-misc 解出多分低，
  mid-web 解出少分高 → 期望 mid-web 和 easy-misc 排在前面)

---

## 10. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 真实 API 与假设不符 | Adapter 层隔离；live.py 只动一个文件；mock 保持全链路可测 |
| Codex OAuth refresh 多容器冲突 | §7.3 实测 + 两级降级方案 (独立快照重同步 / 放弃 Docker) |
| 盲拷配置导致容器内 codex 启动异常 (mcp_servers 指向 /Applications、macOS 绝对路径) | §7.1 精制快照: 剔除桌面版配置 + 路径重写 + 生成最小 config.toml；Phase 2 验收含 profile/hook 触发验证 |
| codex-cli 版本行为差异 (`--profile` 机制) | 镜像锁定 codex-cli 0.147.0，与宿主机一致 |
| 赛方靶机名额限制 | 即用即开即释放；dispatch 失败放回队尾重试；stop_challenge 失败仅告警不阻塞 |
| 提交触发平台惩罚 | 频控 + 单题提交上限；错误 flag 后不立即重试同值 |
| Master 崩溃 | master_state.json 持久化，重启恢复题目状态 |
| 并发 solver 相互干扰 | 容器级隔离 (Phase1 为进程组 + 独立 work_dir + socket 按 work_dir 哈希已天然隔离) |
| LLM 调度输出不可靠 | 仅作排序修正，解析失败回退规则层；可配置关闭 |
| 镜像工具链缺失 (ffuf 等) | Dockerfile 装齐 README「待改」清单中的工具；镜像构建后跑冒烟验证 |

---

## 11. 明确不做的事 (Out of Scope)

- Master 不读 codex.log 做解题级判断（那是容器内 Hermes 的职责）
- 不做跨题知识共享 / 题间 memory 迁移 (设计文档 11.3 的后续项)
- 不改现有 Solver 的任何代码协议 (progress.md/board.md/branch.py 保持原样)
- 不做 Hermes 人机交互增强 (README「待改」项，与本需求无关)
