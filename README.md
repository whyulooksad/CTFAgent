# CTFAgent

Claude Code 解题 + Hermes 监督 + subagent 并行试探的 CTF 自动解题系统，外加 **Master 多题并行调度层**：比赛时从赛方 API 拉题、分发到多个 Docker 隔离的 Solver 并行解题、自动提交 flag。

> 2026-08-18 引擎替换：codex → **claude code**。赛事环境只允许国产大模型，codex 接入
> deepseek 后工具调用崩溃（"No tool output found for tool call"）；claude code 通过
> `ANTHROPIC_BASE_URL` 环境变量接入任意 Anthropic Messages 兼容端点（Cairn 验证过的
> 模式），工具调用/hook/subagent 全部原生可用。引擎配置在 **llm.yaml**（手动编辑，
> docker 运行时挂载——换模型不用重建镜像）。

```
┌──────────────────────────────────────────────────────────────┐
│                     Master (调度层, master/)                   │
│  拉题 → 优先级排序(规则+LLM) → 分发 → 监控 → 自动提交(频控)      │
│  假flag清除+dead_ends反馈 | 双死门回收槽位 | 总览面板 :8081      │
└──────────────┬───────────────────────────────────────────────┘
               │ 每题一个实例 (进程 / Docker 容器, 销毁重建) --managed
               ▼
┌──────────────────────────────────────────────────────────────┐
│              Solver × N (解题层, solver/, 容器内完整复刻)         │
│  Claude Code (解题, llm.yaml 接入) ← guidance/dead_ends 注入     │
│  ← Hermes (监督, 不先死: wrong 反馈写 dead_ends)                 │
│  scout subagent (Task 工具并行试探) | 单题面板 :8080             │
└──────────────────────────────────────────────────────────────┘
通信: Master 只读各题 work_dir/progress.md 的 Flags Found 段检测 flag，
Solver 内部协议 (board/guidance/dead_ends) 零改动。
```

## 目录树

```
CTFAgent/
├── README.md                        # 本文件
├── TOOLS.md                         # 环境工具手册 (解题 Agent 按需读取; 含二进制题流程)
├── llm.yaml                         # 大模型引擎接入 (gitignore, 手动编辑; 容器挂载热替换)
│
├── master/                          # ── Master 调度层 ──
│   ├── master.py                    # 调度主循环: 拉题/分发/监控/重试/收尾/假flag处理/双死门
│   ├── master_dashboard.py          # 总览面板后端 (HTTP+SSE, :8081, 含 /api/connect-platform)
│   ├── master_dashboard.html        # 总览面板前端 (题目卡片 + 双日志流 + 模型平台接入表单)
│   ├── challenge_state.py           # 题目状态机 + master_state.json 持久化 + flag 提取
│   ├── prioritizer.py               # 优先级: 规则层(分高+容易优先) + LLM(claude -p)软修正
│   ├── llm_config.py                # llm.yaml 读写 + ANTHROPIC_* 环境注入 + api_key 掩码
│   ├── solver_pool.py               # Solver 后端: ProcessBackend / DockerBackend / FakeBackend
│   ├── submitter.py                 # flag 自动提交: 频控 + 单题上限 + 退避重试
│   ├── cred_snapshot.py             # hermes 凭据快照 (claude code 走环境变量, 无需快照)
│   ├── master_config.json           # 默认配置
│   ├── master_config.smoke.json     # 手动调试: 进程后端 + mock 3 题
│   ├── master_config.docker.json    # 手动调试: Docker 后端 + mock 3 题
│   ├── master_config.demo.json      # 零成本面板演示 (fake 后端, 不起 claude)
│   └── adapters/                    # 赛方平台适配层
│       ├── base.py                  # 抽象接口 + Challenge/SubmitResult 数据结构
│       ├── mock.py                  # mock 平台: 3 道本地假题, web 题本地起靶机
│       ├── manual.py                # 手动题池 (面板「加题」, 无判定平台恒 correct)
│       ├── tsec.py                  # 腾讯 Tsecbench (BENCHMARK_TOKEN + VPN 直连)
│       └── live.py                  # 通用平台骨架 (best-effort)
│
├── solver/                          # ── Solver 解题层 (单题) ──
│   ├── run.sh                       # 单题入口: llm.yaml 加载 + claude -p 续跑 + Hermes 监控
│   ├── AGENT.md                     # 解题 Agent 系统指令 (--append-system-prompt-file 注入)
│   ├── hermes_monitor.md            # Hermes 监督 agent 指令 (含 [master] 假flag通知处理)
│   ├── monitor.py                   # Hermes 的眼睛: 10s 轮询 agent.log 增量
│   ├── dashboard.py                 # 单题面板 (HTTP+SSE, :8080)
│   ├── dashboard.html               # 单题面板前端
│   └── hooks/
│       └── check_guidance.py        # PostToolUse hook: 注入 guidance/dead_ends 后清空
│
├── docker/
│   └── solver/                      # ctf-solver 镜像
│       ├── Dockerfile               # python3.11 + CTF 工具链 + claude code + hermes
│       ├── entrypoint.sh            # 容器入口 -> solver/run.sh
│       ├── build.sh                 # hermes 源同步 + docker build (默认国内镜像源)
│       ├── SOLVER_SYNC.md            # 同步/变更记录 (main→master-agent + 引擎替换)
│       └── hermes-src/              # (构建时从 ~/.hermes 同步, gitignore)
│
├── tests/
│   ├── test_master.py               # 全量测试 (fake 后端, 不依赖 claude/hermes)
│   ├── fake_claude_llm.sh           # LLM 优先级测试用假 claude
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

运行时还会在仓库根生成（均 gitignore）：`master_state.json`（状态机）、`master.log`、
`master_logs/`（run.sh 输出）、`cred_snapshots/`（hermes 凭据快照）。

## 前置依赖

1. **Claude Code CLI** `npm install -g @anthropic-ai/claude-code`（镜像内锁定 2.1.220，
   宿主保持同版本）。比赛环境经 llm.yaml 接入赛方国产大模型平台，**无需登录**；
   本地开发不填 llm.yaml 则用本机 claude 默认登录态
2. **Hermes Agent** 已安装（`hermes chat -q` 可用）+ skill `ctf-supervisor-knowledge`
3. **Python 3**（标准库即可，零第三方依赖）
4. Docker（仅 Docker 后端需要）
5. CTF 工具按需（进程后端用宿主机的；容器镜像已装齐 nmap/sqlmap/ffuf/gobuster/dirsearch/wfuzz/binwalk/steghide/exiftool/dirb 字典/tshark/foremost/gdb/pwntools/z3/pycryptodome，手册见 `TOOLS.md`）

## 大模型引擎配置 (llm.yaml，文件级)

claude code 通过环境变量接入任意 **Anthropic Messages API 兼容端点**。llm.yaml 是
**纯文件配置**（不在面板里填——面板的「平台接入」是赛方**题目**平台，见下节）：

```yaml
platform: "DeepSeek"                              # 平台名称 (仅展示)
base_url: "https://api.deepseek.com/anthropic"    # 兼容端点
api_key: "sk-xxx"                                 # 平台密钥
model: "deepseek-chat"                            # 模型名 (同时用作后台小模型)
effort: ""                                        # 可选思考档位 low|medium|high|xhigh|max

# ── Hermes 监督引擎接入 (留空 = 沿用 ~/.hermes 自己的 provider/凭据池) ──
hermes_provider: "deepseek"                       # hermes 内置 provider 名
hermes_base_url: "https://api.deepseek.com"       # OpenAI 兼容端点 (与上方 /anthropic 不同!)
hermes_api_key: ""                                # 留空沿用上方 api_key
hermes_model: ""                                  # 留空沿用上方 model
```

- **换模型不用重建镜像**：llm.yaml 不进镜像（.dockerignore 排除），DockerBackend 每次
  `docker run` 时只读挂载到 `/opt/ctf-agent/llm.yaml:ro`——改文件后新分发的 solver 即刻
  用新配置；进程后端每题新起 run.sh，同样即时生效。顶栏「模型」徽章显示当前配置
  （key 掩码）
- **hermes 同样热切换**：run.sh 导出 `<PROVIDER>_API_KEY` / `<PROVIDER>_BASE_URL` 并给
  每次 `hermes chat` 附加 `--provider` / `-m`，监督引擎与解题引擎共用赛方平台
  （hermes 走 OpenAI 兼容协议，端点与 claude 的 /anthropic 是两条不同地址）
- gitignore 排除，密钥不进仓库；解析是标准库扁平 yaml（不依赖 pyyaml）
- 消费方：`solver/run.sh` 启动时导出 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` /
  `ANTHROPIC_MODEL` / `ANTHROPIC_SMALL_FAST_MODEL`（+可选 `--effort`）与 hermes 对应变量；
  `master` 启动时同样注入（LLM 优先级的 `claude -p` 子进程继承）

## 解题引擎机制 (claude code)

| 机制 | 实现 |
|------|------|
| 非交互解题 | `claude -p <prompt> --dangerously-skip-permissions --output-format stream-json --verbose`，事件流写 `agent.log`（过滤 thinking_tokens 噪音） |
| 系统指令 | `solver/AGENT.md` 经 `--append-system-prompt-file` 注入（不在仓库根放 CLAUDE.md，避免污染开发会话） |
| **Hermes 指导注入** | 每次 run.sh 生成 `.claude_settings.json`（`--settings` 传入），PostToolUse hook 调 `solver/hooks/check_guidance.py`：guidance.md/dead_ends.md 有新内容 → additionalContext 注入 → 读后清空（已实测 -p 模式可用，与 codex 时代机制等价） |
| **subagent 并行试探** | 原生 Task 工具 + `--agents` 定义的 `scout` subagent（只侦察验证、输出 FEASIBLE/INFEASIBLE），**branch.py daemon 已退役** |
| 多轮续跑 | run.sh 最多 10 轮，每轮全新会话读 board.md/progress.md 恢复上下文 |

## Master 调度关键机制

### 调度规则层 v2（效用分模型，2026-08-18 真机复盘后重构）

```
utility = 期望得分 / 期望耗时 × 方差惩罚 × 系列修正      (每槽位小时的期望得分)

期望得分 = P(解出|难度) × score × (1 + 0.5×(flag数-1))   多 flag 按"每 flag 一份分"乐观计
期望耗时 = T(难度) × (1 + 0.4×(flag数-1))
方差惩罚 = 1 / (1 + 0.15×(flag数-1))
```

- **P/T 先验**：easy 0.85/15min，medium 0.45/30min，hard 0.20/50min
- **score 用平台动态分值**（解出人数越多分越低、底 80%）——天然编码热度，无需独立
  解出人数字段；tsec 适配器不再造假设（旧版把难度塞进 solve_count，已解列全是假数）
- **多 flag 夹层**：排序夹在"同难度单 flag"与"下一档难度"之间——不会像旧 ÷4 规则
  沉底，也绝不会跳到 easy 前面（b 系跳队事故的反向保障）
- **系列学习**：同前缀（a-/b-/e1-…）历史成败经拉普拉斯平滑修正 P（系数 ∈ [0.5,1.5]，
  尝试≥2 才启用）——某系连败自动整体降权，纯规则零 LLM
- **分层超时**：`T(难度)×(1+0.4×(flag数-1))×1.5`，clamp [20,75]min（easy≈22min、
  hard 封顶 75min）；难度未知（手动题）用全局 `solver_timeout`
- **多 flag 提交逻辑**：解出一个立即提交（分数落袋）→ 继续攻剩余 → 分层超时或轮次
  上限 kill；重试仅限"高分"或"多 flag 已有部分进度"
- **LLM 软修正默认关**（`llm_priority: false`）：真机实测 deepseek 重排把多 flag 排
  队首 + 每次分发多 30-60s 延迟；配置可重新启用
- **手动题默认分 250**（高于平台动态分 ≈200-500 区间内的常见值）：手动加题是用户
  明确意图，不排在平台题后面饿死（面板可改）


- **假 flag 处理**（提交 wrong）：① 当场从 progress.md 的 Flags Found 段清除该 flag
  （段空恢复 `(无)`）② 写 `[master]` 通知进 work_dir/human_guidance.md → Hermes 消费并
  写 dead_ends.md（硬约束：禁止重交、绕开产出路径）→ hook 注入 → 解题 Agent 下一轮
  绕开继续挖。flags_seen 保留保证同一 flag 不会重复提交
- **真 flag**：correct 后保留在 progress.md（多 flag 题同一 solver 持续攻坚，拿满才收工）
- **双死门**（防槽位泄漏）：master 回收 solver 前必须确认 **claude + hermes 都死了**——
  写 STOP 文件 → 同步 stop（SIGINT→8s→SIGKILL / docker stop）→ 复查 is_alive==False
  才关靶机、释放槽位、请求下一题。hermes 监控循环在 run.sh 进程组内，run.sh 退出前还会
  等在途 hermes 周期写完（`.hermes_busy` 标记，最多 30s），保证 dead_ends 不被截断
- **--managed 模式**：master 分发的 run.sh 带 `--managed`——不因 Flags Found 出现 flag
  提前退出（对错由平台判定），看到 STOP 文件才收工；独立直跑（无 --managed）保持
  "拿到 flag 即退出"的旧行为

## 启动

### 0. 日常使用（常驻模式，默认）

```bash
python3 master/master.py        # 默认配置: adapter=none + resident=true + standby_start=true
```

- **待命启动**：命令启动后**只起静态面板，不拉题不调度**（顶栏「待接入」徽章）；
  在「平台接入」填好赛方**题目平台**（名称/base_url/api_key）点「接入」才**开始调度**——
  接入即热切换 adapter 并拉题，api_key 提交后即清、面板只回显掩码
- **解题进度按 api_key 隔离**：接入时按（平台名 + api_key）定位进度文件
  `master_state_<平台名>_<key哈希>.json`——同一 key 重启/重连 = **续跑该轮进度**
  （不重复请求已解出的题，Flags 面板回填该轮历史）；换新 key = **全新一轮**（新文件，
  首次保存时创建）。待命期间面板保持干净（不加载任何历史状态）
- 进程**常驻**，面板 **http://localhost:8081** 永远在线（测试结束也不退出），`Ctrl+C` 停止
- **平台接入**（赛方题目 API：拉题/开靶机/提交 flag）：填平台名称/base_url/api_key，
  base_url 含 `tsecbench` 自动用 TSecAdapter（BENCHMARK_TOKEN 认证 + VPN 预检），
  其余用通用 LiveAdapter
- **手动加题**（测其他 CTF 平台的题）：选类型（web 填靶机 URL / crypto、misc 填本地附件
  绝对路径）+ 描述，右侧「＋」加行、一次提交多题，统一进 master 调度。手动题不受题量
  上限约束；手动模式**只展示不提交**——找到 flag 即闭环，在 Flags 面板复制后自行去目标
  平台提交（卡片带「手动」标）
- **Flags 面板**：滚动列表展示**本次启动**解出的所有 flag（API 模式标「API 已提交」、
  手动模式标「手动」），历史归档在仓库根 `flags.jsonl`
- 调度配置区可运行时改并发数/题量上限

### 1. TSec 平台真机跑分（腾讯 tsecbench）

```bash
# 前置: 平台 VPN 已连 (预检点 http://10.0.100.58 返回 status:ok)
# 方式一 (面板接入, 推荐): 启动后待命，面板「平台接入」填 TSec / 平台地址 / api_key
#   (即跑分任务的 BENCHMARK_TOKEN) 点「接入」开始拉题调度
# 方式二 (环境变量): TSEC_TOKEN 已设则启动时直接接入
TSEC_TOKEN="<平台任务token>" \
TSEC_EXCLUDE_PREFIXES="b" \
  python3 master/master.py --config master/master_config.tsec.json
```

- `TSEC_TOKEN`：平台创建跑分任务时返回的 UUID（请求头 `BENCHMARK_TOKEN`，非网站登录
  token；有效期短，跑前现取）。环境变量直连时进度文件同样按 token 隔离：
  `master_state_tsec_<token哈希>.json` / `flags_tsec_<token哈希>.jsonl`（与面板接入的
  api_key 隔离同款机制），历史保留不删——新 token 全新进度，同 token 重启正确恢复
- `TSEC_EXCLUDE_PREFIXES`：排除不做的题系（逗号分隔题号前缀，如 `"b,f2"`；默认空）
- 平台 63 题 6 大维度：`a`=web 挖掘、`b`=多阶段渗透(多flag)、`c`=面板渗透、`d`=云、
  `e1/e2/e3`=对抗规避——均按 **web** 流程调度；`f1/f2`=二进制——按 **binary** 流程
  （TOOLS.md 二进制章节 + 镜像内 pwntools/gdb/binutils/z3 工具链）
- 活跃靶机上限 3（与 `max_solvers=3` 对齐），解出自动提交，通关自动 close 释放名额
- 多 flag 题（b 系 4-6 个）：**同一 solver 持续攻坚**；面板显示 `⚑×N 已得M`；
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

# 小成本: 进程后端 + 真实 claude/hermes 解 mock 3 题 (~5 分钟, 需 llm.yaml 或本机登录态)
python3 master/master.py --config master/master_config.smoke.json

# Docker 后端: 3 容器并发 (需先构建镜像)
CTF_MOCK_PUBLIC_HOST=host.docker.internal \
  python3 master/master.py --config master/master_config.docker.json
```

面板统一开 **http://localhost:8081**：题目卡片（状态/分数/尝试/时长/flag）、claude+hermes
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
python3 tests/test_master.py   # 9 项: flag提取/排序/LLM回退/面板API/手动+常驻/多flag防死循环/假flag+双死门/端到端
```

## Docker 构建

```bash
bash docker/solver/build.sh          # 同步 ~/.hermes 源码 + 构建镜像 (默认国内源)
docker/solver/build.sh --no-sync     # 跳过 hermes 同步
```

- 镜像内 claude code 锁定 2.1.220（`@anthropic-ai/claude-code`）、hermes 以 editable
  方式装进 Linux venv（macOS 的 venv 二进制不能拷贝）、项目以 git 仓库形态烘焙进
  `/opt/ctf-agent`
- 镜像内置 `IS_SANDBOX=1`（root 容器内允许 `--dangerously-skip-permissions`）与
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`（关非必要外联）
- **架构跟随构建机**：Apple Silicon 构建 = arm64；**WSL x86 机器上需重新跑 build.sh**
  得到 amd64 镜像
- TUNA pypi 偶发个别 wheel 403 时换阿里云源：
  `PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ bash docker/solver/build.sh`

### 凭据快照（cred_snapshot.py）与容器接入

Master 用 docker 后端时自动生成（也可手动 `python3 master/cred_snapshot.py`）。
**引擎切换 claude code 后快照只剩 hermes**：

| 内容 | 容器内处理 | 目的 |
|---|---|---|
| `~/.hermes/` 的 auth.json/config.yaml/.env | 选择性拷贝 + home 路径重写 | hermes 登录态 |
| `~/.hermes/skills/` | 整目录复制 | run.sh 的 `-s ctf-supervisor-knowledge` 可用 |
| llm.yaml | **不进镜像**，DockerBackend 运行时只读挂载到 `/opt/ctf-agent/llm.yaml` | claude code 接入赛方平台，api_key 不落镜像 |

claude code 本体在容器内**零凭据**：全部靠环境变量（llm.yaml 由 run.sh 导出），
`--no-session-persistence` 不落会话文件，容器销毁即无残留。

## 网络环境注意事项（重要，踩过的坑）

- **全直连，无代理**：引擎接入国产大模型平台（deepseek 等，国内直连），靶机在 VPN
  内网——代码不注入任何代理（codex/gpt 时代自动探测宿主代理并注入容器的链路已于
  2026-08-18 移除，`PROXY_FOR_CONTAINERS` 环境变量同步废弃）
- **Docker Desktop「系统代理」是大坑**：它会在虚拟机网络层透明劫持容器 80 端口流量
  送进 Clash；Clash 若把内网段（如 `10.0.160.x`）送去公网节点 → 靶机全变 502 假响应
  （题本身是好的）。两种解法任选：① Docker Desktop 设置代理为 manual 且清空；
  ② Clash 加规则 `IP-CIDR,10.0.0.0/8,DIRECT`

## Master 配置项（master_config.json）

| 键 | 默认 | 说明 |
|------|------|------|
| adapter | none | 平台适配器: none(手动+面板接入) / mock / tsec(腾讯跑分) / live(通用) |
| resident | true | 常驻模式: 不自动退出，面板永远在线 (0 = 跑完队列自动退出) |
| standby_start | true | 待命启动: 先只起面板不调度，面板「接入」赛方题目平台 (拉题 API) 后才开始 (测试/调试配置里为 false) |
| backend | process | solver 后端: process / docker / fake(调试) |
| max_solvers | 5 | 并行 solver 槽位数 (面板可改) |
| max_challenges | 20 | 尝试题目数上限 (去重计，重试不占) |
| solver_timeout | 3600 | 单 solver 整体超时 (秒)；平台题按难度×flag数分层覆盖 [20,75]min，此值为难度未知时的全局兜底 |
| max_retries_per_challenge | 1 | 失败重试上限，仅限高价值题 |
| retry_value_threshold / retry_rarity_threshold | 0.6 / 0.7 | 高价值判定阈值 |
| poll_interval | 15 | 主循环间隔 (秒) |
| llm_priority | false | LLM 优先级软修正开关 (默认关: 真机实测 deepseek 重排把多 flag 排前且每次分发多 30-60s；规则层单干) |
| submit_min_interval / max_submit_per_challenge | 10 / 3 | 提交频控 |
| dashboard_port | 8081 | 0 = 关面板 |

## 运行产物速查

| 想看什么 | 位置 |
|---|---|
| 调度决策日志 | `master*.log`（仓库根） |
| 历次解出的 flag 归档 | `flags.jsonl`（面板 Flags 区只展示本次启动） |
| 题目状态机 | `master_state*.json`（按 平台+api_key 作用域: `master_state_<平台>_<key哈希>.json`） |
| 某题解题现场 | `challenges/manual_<hash>/`（progress/board/agent.log/hermes.log） |
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
   | monitor.py / hermes_monitor.md / dashboard.py / dashboard.html | solver/ | 直接覆盖（main 独有优化），随后把 codex 引用替换为 claude 语义 |
   | run.sh | solver/ | **手工合并**（见第 2 步） |
   | AGENTS.md | solver/AGENT.md | 手工合并（claude 用 `--append-system-prompt-file` 注入，**不放仓库根**） |
   | TOOLS.md | **仓库根** | 直接覆盖（AGENT.md 引用 `../../TOOLS.md`） |
   | branch.py | — | **不合并**（已退役，原生 Task 工具 + scout agent 替代） |

   不同步：main 的 challenges/ 运行产物、README、设计文档。

2. **重放 master 适配**（solver 文件里 master 分支埋的改动，丢了就出事故）：
   - `run.sh`：`--flag-count N`（多 flag 拿满才退出）+ `--type binary` + `--managed`
     （STOP 文件收工）+ llm.yaml 环境加载 + claude -p 调用参数（settings/agents/
     stream-json）+ Flags Found 段噪音过滤（防假闭环）+ `REPO_ROOT` 路径 +
     `.hermes_busy` 收尾等待
   - `AGENT.md`：Flags Found 段只写 flag 本身的约束 + scout subagent 用法
   - `dashboard.py`：`CHALLENGES_DIR` 指向仓库根 + agent.log 命名
   - 若 main 又加了新的 hermes skill 调用：确认 skill 已装宿主机（`hermes skills list`），
     cred_snapshot 会把 `~/.hermes/skills/` 带进容器

3. **检查环境差异**：main 的 TOOLS.md/AGENT.md 若引用新工具 → 更新
   `docker/solver/Dockerfile`（Debian 源没有的包不能加，如 seclists 是 Kali 专属）

4. **验证**：`python3 tests/test_master.py`（9/9）→ `master_config.smoke.json`
   进程后端 mock 一轮（3/3）→ 需要容器时重建镜像跑 docker 冒烟

5. **记录**：在 `docker/solver/SOLVER_SYNC.md` 追加一行（main commit 哈希 +
   重放清单），镜像 tag 用 `ctf-solver:<日期>-<main短哈希>` 便于回滚

## 设计文档

- `docs/ctf-agent-design.md` -- Solver 层（解题 Agent/Hermes/subagent 协作、文件协议、看板）
- `docs/master-agent-spec.md` -- Master 层完整规格：状态机、重试规则、Docker 化方案、
  四阶段开发记录与决策表

## 待办

- [x] ~~Phase 4: 真实 API 对接~~ —— TSec 平台已接入并真机验证（2026-08-16，单轮 9 题
      解出提交：a 系列 web + c 系列面板，b 系列多 flag 与 f 系列 binary 各验证 1 题）
- [x] ~~codex → claude code 引擎替换~~（2026-08-18，llm.yaml 接入 + hook/subagent 原生化 +
      假flag/双死门机制，fake 后端 9/9 测试通过；真机冒烟待 llm.yaml 填写后执行）
- [ ] 真机冒烟: 工具调用 / scout subagent 并行试探 / 完整流程 (等 llm.yaml)
- [ ] WSL 上重建 amd64 镜像并复跑 docker 冒烟（比赛机迁移 + claude code 镜像验证）
- [ ] hermes 可能需要更多 CTF 做题技巧，且人应该可以和 Hermes 交互
- [ ] hermes 监控输出偶尔超限（ARK API max_token），考虑更大 token 模型或压缩管理
- [ ] board.md 容量管理（8 ideas + 12 memory 偏小，改大还是做压缩）
- [ ] f 系（二进制）真机仅验证 1 题，f2 固件类未实战检验

## 已解决的实战问题（防复发备忘）

| 现象 | 根因 | 修复 |
|---|---|---|
| codex 接 deepseek 后工具调用崩溃 "No tool output found for tool call" | codex 与国产模型的工具调用协议不兼容 | 引擎整体替换为 claude code（ANTHROPIC_BASE_URL 接 anthropic 兼容端点，工具调用/hook/subagent 原生） |
| 靶机全 502、solver 空转 | Docker Desktop 系统代理劫持容器流量送 Clash，内网段被送公网节点 | Docker 代理改 manual 清空 / Clash 加 `10.0.0.0/8,DIRECT` |
| b 系列多 flag 反复"解出"同一 flag | duplicate 被当 correct + flags_seen 被清空 → 死循环 | duplicate 不计分不回收；flags_seen 保留；同 solver 持续攻坚 |
| recon 阶段 solver 被误杀 | 解题 Agent 把进度笔记写进 Flags Found 段被当 flag | 提取端"像 flag"过滤 + AGENT.md 约束 + run.sh 同款过滤 |
| 手动题失败被莫名重试 | 0分0解被 rarity 公式判"高价值" | 手动题不自动重试；web 重试前靶机探活 |
| 腾讯侧 3 容器、master 面板只有 2 个在跑，其余"靶机不可达" | 平台 start 返回地址时容器仍在启动，5s 预检误判不可达 → 终态+close 刚开的容器 → close 超时泄漏平台槽位 → 后续 start 撞 409 | 平台题预检失败改冷却等待（约 4 分钟窗口，容器就绪后复用同地址）；close 失败退避重试 3 次 |
| 新 token 启动带着旧 token 的 submitted_correct / 重试计数 | 状态文件跨 token 复用 | 状态/flag 文件按 (平台名, api_key/token 哈希) 作用域隔离，历史保留；待命期不加载任何状态，面板保持干净 |
| hermes 会话复用失效 | run.sh 用了 BSD grep 不支持的 `grep -P` | 改 POSIX sed 提取 session_id |
| 靶机已 close 但解题进程还在跑（槽位泄漏复发风险） | master 停 solver 后不等 claude/hermes 死透就关靶机 | 双死门 `_terminate`：STOP 文件 + 同步 stop + is_alive 复查死透才释放；run.sh 退出前等在途 hermes 周期写完 |
| 真机多 flag 的 b 系跳到队首、每次分发间隔 30-60s | LLM 优先级层 (claude -p/deepseek) 对 63 题的重排不可靠且每次分发阻塞等它 | llm_priority 默认关 + 规则层 v2 效用分模型（多 flag 夹层 + 系列学习 + 分层超时） |
| 快题 (d-01/d-02 约 30s 解完) 全程无监督 | hermes 首次调用冷启动 (CLI+skill 预载+建会话+推理模型首响应) > 快题存活时间 | run.sh 监控循环启动即预热 (建会话不等日志)，后续触发复用会话 |
