#!/bin/bash
#
# run.sh -- CTF Agent 启动脚本 (解题引擎: claude code)
#
# 用法:
#   ./run.sh --type web --url "http://target:8080" --hint "SQL注入"
#   ./run.sh --type crypto --attachment "/path/to/challenge.zip" --hint "RSA"
#   ./run.sh --type misc --attachment "/path/to/file.zip" --hint "隐写"
#   ./run.sh --type binary --url "http://target:9999" [--attachment ./pwn.bin] --hint "栈溢出"
#   多 flag 题: 追加 --flag-count N (独立模式下拿满 N 个才退出)
#   master 调度: 追加 --managed (由 master 写 STOP 文件决定收工，
#                不因 Flags Found 出现 flag 提前退出 —— flag 对错由平台判定)
#
# 功能:
#   1. 创建挑战工作目录 + 初始化文件
#   2. 读仓库根 llm.yaml -> 导出 claude code 接入环境变量 (国产大模型平台)
#   3. 启动 Hermes 监控 (background loop, 输出写 hermes.log)
#   4. 自动续跑 claude -p (最多 10 轮，PostToolUse hook 注入 hermes 指导)
#   5. 退出前等在途 hermes 周期写完 (dead_ends 不截断) 再清理
#
# claude code 接入 (llm.yaml，Cairn 验证过的模式):
#   ANTHROPIC_BASE_URL   赛方平台 Anthropic Messages 兼容端点
#   ANTHROPIC_AUTH_TOKEN 赛方平台 api_key
#   ANTHROPIC_MODEL / ANTHROPIC_SMALL_FAST_MODEL  模型名
#   llm.yaml 缺失/留空时按本机 claude 默认登录态运行 (本地调试)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"   # 仓库根 (challenges/ 与 TOOLS.md 所在)

# ─── 参数解析 ───

CHALLENGE_TYPE=""
TARGET_URL=""
ATTACHMENT=""
HINT=""
FLAG_COUNT=1
MANAGED=0
CID=""

while [ $# -gt 0 ]; do
    case "$1" in
        --type)       CHALLENGE_TYPE="$2"; shift 2 ;;
        --url)        TARGET_URL="$2"; shift 2 ;;
        --attachment) ATTACHMENT="$2"; shift 2 ;;
        --hint)       HINT="$2"; shift 2 ;;
        --flag-count) FLAG_COUNT="$2"; shift 2 ;;
        --managed)    MANAGED=1; shift ;;
        --cid)        CID="$2"; shift 2 ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

if [ -z "$CHALLENGE_TYPE" ]; then
    echo "用法:"
    echo "  $0 --type web --url \"http://target:8080\" --hint \"背景\""
    echo "  $0 --type crypto --attachment \"/path/to/file.zip\" --hint \"背景\""
    echo "  $0 --type misc --attachment \"/path/to/file.zip\" --hint \"背景\""
    exit 1
fi

case "$CHALLENGE_TYPE" in
    web)
        if [ -z "$TARGET_URL" ]; then echo "web 类型需要 --url"; exit 1; fi
        # 用 MD5 短哈希避免 URL 带路径时目录名过长
        SHORT_HASH=$(printf '%s' "$TARGET_URL" | md5sum | cut -c1-12)
        WORK_DIR_NAME="manual_web_${SHORT_HASH}"
        ;;
    crypto|misc)
        if [ -z "$ATTACHMENT" ]; then echo "$CHALLENGE_TYPE 类型需要 --attachment"; exit 1; fi
        if [ ! -f "$ATTACHMENT" ]; then echo "附件不存在: $ATTACHMENT"; exit 1; fi
        # 复制附件到工作目录
        ATTACHMENT_NAME=$(basename "$ATTACHMENT")
        SHORT_HASH=$(printf '%s' "$ATTACHMENT" | md5sum | cut -c1-12)
        WORK_DIR_NAME="manual_${CHALLENGE_TYPE}_${SHORT_HASH}"
        ;;
    binary)
        # 二进制题: 远程服务 (URL) + 可选附件 (二进制制品/固件)
        if [ -z "$TARGET_URL" ]; then echo "binary 类型需要 --url (远程服务地址)"; exit 1; fi
        if [ -n "$ATTACHMENT" ] && [ ! -f "$ATTACHMENT" ]; then
            echo "附件不存在: $ATTACHMENT"; exit 1
        fi
        SHORT_HASH=$(printf '%s' "$TARGET_URL" | md5sum | cut -c1-12)
        WORK_DIR_NAME="manual_binary_${SHORT_HASH}"
        ;;
    *)
        echo "未知题目类型: $CHALLENGE_TYPE (支持: web, crypto, misc, binary)"
        exit 1
        ;;
esac

# 工作目录命名:
#   master 分发 (--cid): <题目id>_<题型> —— 每题唯一 (TSec 会回收同一靶机地址再分给
#     下一题，旧 md5(url) 命名会让两题共用目录: 日志串台/复盘被覆盖)；同题重试复用
#     同一目录 (保留 hermes 的 dead_ends/guidance 跨尝试连续性)
#   独立直跑 (无 --cid): 沿用旧 manual_<题型>_<md5(url/附件)[:12]> 命名
if [ -n "$CID" ]; then
    SAFE_CID=$(printf '%s' "$CID" | tr -c 'A-Za-z0-9_.-' '_')
    WORK_DIR_NAME="${SAFE_CID}_${CHALLENGE_TYPE}"
fi

MAX_RETRIES=10
WORK_DIR="$REPO_ROOT/challenges/$WORK_DIR_NAME"

echo "=== CTF Agent 启动 (claude code) ==="
echo "Type: $CHALLENGE_TYPE"
case "$CHALLENGE_TYPE" in
    web)        echo "Target: $TARGET_URL" ;;
    crypto|misc) echo "Attachment: $ATTACHMENT" ;;
esac
[ "$MANAGED" = "1" ] && echo "Managed: master 调度模式 (STOP 文件收工)"
echo "Work dir: $WORK_DIR"
echo ""

# ─── 赛方大模型平台配置 (llm.yaml -> claude code / hermes 环境变量) ───
# 扁平 yaml 解析 (与 master/llm_config.py 同款语义，无 pyyaml 依赖)
CLAUDE_EFFORT=""
HERMES_PROVIDER=""
HERMES_MODEL=""
if [ -f "$REPO_ROOT/llm.yaml" ]; then
    eval "$(python3 - "$REPO_ROOT/llm.yaml" <<'PYEOF'
import shlex, sys
cfg = {}
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.split("#", 1)[0].strip()
    if not line or ":" not in line:
        continue
    k, v = line.split(":", 1)
    cfg[k.strip()] = v.strip().strip('"').strip("'")
env = {
    "ANTHROPIC_BASE_URL": cfg.get("base_url", ""),
    "ANTHROPIC_AUTH_TOKEN": cfg.get("api_key", ""),
    "ANTHROPIC_MODEL": cfg.get("model", ""),
    "ANTHROPIC_SMALL_FAST_MODEL": cfg.get("model", ""),
}
for k, v in env.items():
    if v:
        print(f"export {k}={shlex.quote(v)}")
if cfg.get("effort", "").strip():
    print(f"export CLAUDE_EFFORT={shlex.quote(cfg['effort'].strip())}")
# hermes 监督引擎接入: provider 内置注册表按 <名称大写>_API_KEY/_BASE_URL 读环境变量。
# key/model 留空沿用主配置 (hermes 走 OpenAI 兼容端点，base_url 必须显式给)
hp = cfg.get("hermes_provider", "").strip()
if hp and cfg.get("hermes_base_url", "").strip():
    prefix = hp.upper().replace("-", "_")
    key = cfg.get("hermes_api_key", "").strip() or cfg.get("api_key", "")
    if key:
        print(f"export {prefix}_API_KEY={shlex.quote(key)}")
    print(f"export {prefix}_BASE_URL={shlex.quote(cfg['hermes_base_url'].strip())}")
    print(f"export HERMES_PROVIDER={shlex.quote(hp)}")
    hm = cfg.get("hermes_model", "").strip() or cfg.get("model", "")
    if hm:
        print(f"export HERMES_MODEL={shlex.quote(hm)}")
PYEOF
)"
fi
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
# 已接入平台时显式留痕 (面板/日志排查用；key 不回显)
if [ -n "${ANTHROPIC_BASE_URL:-}" ] && [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
    echo "[run.sh] LLM 平台已接入: ${ANTHROPIC_BASE_URL} (model: ${ANTHROPIC_MODEL:-默认})"
else
    echo "[run.sh] 未配置 llm.yaml 接入信息，按本机 claude 默认登录态运行"
fi
if [ -n "${HERMES_PROVIDER:-}" ]; then
    echo "[run.sh] Hermes 接入 llm.yaml: provider=${HERMES_PROVIDER} model=${HERMES_MODEL:-默认}"
fi

# ─── 初始化工作目录 ───

# 清理上一次运行的残留状态（同一 URL 会复用工作目录）
rm -f "$WORK_DIR/agent.log" "$WORK_DIR/hermes.log" "$WORK_DIR/monitor_state.json"
rm -f "$WORK_DIR/STOP"          # 新生命周期: 上次的 STOP 作废 (managed 模式)
rm -f "$WORK_DIR/.hermes_busy"

mkdir -p "$WORK_DIR/poc_scripts"

# crypto/misc: 复制附件到工作目录
if [ -n "$ATTACHMENT" ]; then
    cp "$ATTACHMENT" "$WORK_DIR/"
    ATTACHMENT_IN_WORKDIR="$WORK_DIR/$(basename "$ATTACHMENT")"
fi

# progress.md -- 按类型区分初始内容
case "$CHALLENGE_TYPE" in
    web)
        cat > "$WORK_DIR/progress.md" << EOF
## Target
- Type: web
- URL: $TARGET_URL
- Background: $HINT
- Start Time: $(date -Iseconds)

## Current Phase
recon

## Next Steps
1. curl 探测目标
2. 识别技术栈和入口点

## Key Artifacts

## Flags Found
(无)
EOF
        ;;
    crypto|misc)
        cat > "$WORK_DIR/progress.md" << EOF
## Target
- Type: $CHALLENGE_TYPE
- Attachment: $ATTACHMENT_NAME
- Background: $HINT
- Start Time: $(date -Iseconds)

## Current Phase
recon

## Next Steps
1. 解压附件，识别文件类型
2. 分析文件内容，寻找突破口

## Key Artifacts

## Flags Found
(无)
EOF
        ;;
    binary)
        cat > "$WORK_DIR/progress.md" << EOF
## Target
- Type: binary
- URL: $TARGET_URL
- Attachment: ${ATTACHMENT_NAME:-无}
- Background: $HINT
- Start Time: $(date -Iseconds)

## Current Phase
recon

## Next Steps
1. 有附件先 file/strings/checksec 分析制品
2. 探测远程服务协议
3. 寻找内存安全缺陷/逻辑漏洞，构造 exploit

## Key Artifacts

## Flags Found
(无)
EOF
        ;;
esac
cat > "$WORK_DIR/board.md" << 'EOF'
# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|--------|---------|

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
EOF

# 空文件
touch "$WORK_DIR/guidance.md"
touch "$WORK_DIR/dead_ends.md"
touch "$WORK_DIR/human_guidance.md"
touch "$WORK_DIR/hermes.log"

# claude code 会话配置: PostToolUse hook 注入 hermes 的 guidance/dead_ends
# (hook 脚本按 stdin 的 cwd 定位工作目录，读后清空，无内容静默)
cat > "$WORK_DIR/.claude_settings.json" << EOF
{
  "hooks": {
    "PostToolUse": [
      {"matcher": "*", "hooks": [{"type": "command", "command": "python3 $REPO_ROOT/solver/hooks/check_guidance.py", "timeout": 5}]}
    ]
  }
}
EOF

# scout subagent (原生 Task 工具): 并行试探攻击方向，替代原 branch.py daemon
CLAUDE_AGENTS='{"scout": {"description": "CTF 攻击方向试探 subagent: 只侦察验证单个攻击方向是否可行，不深入利用", "prompt": "你是 CTF 试探 subagent。任务: 验证主会话指定的一个攻击方向是否可行。只做侦察和快速验证 (payload 探测/最小 PoC)，不做深入利用。不要写 progress.md (那是主会话的文件)。结论必须是 FEASIBLE 或 INFEASIBLE 开头，附证据 (响应片段/关键输出)。超时意识: 单方向控制在几分钟内。", "tools": ["Bash", "Read", "Write", "Grep", "Glob"], "model": "inherit"}}'

echo "[run.sh] 工作目录初始化完成"

# ─── 启动 Hermes 监控 (background loop, 输出写 hermes.log) ───
# monitor.py 每 10s tail agent.log，有新日志增量时调 hermes agent
# Hermes agent 的输出写入 hermes.log (供 dashboard 实时展示)
# .hermes_busy 标记在途 hermes 周期: run.sh 退出前等它写完 (dead_ends 不截断)
# llm.yaml 配了 hermes 接入时，给每次 hermes chat 附加 --provider/-m (热切换关键)

HERMES_ARGS=""
if [ -n "${HERMES_PROVIDER:-}" ]; then
    HERMES_ARGS="--provider ${HERMES_PROVIDER}"
    if [ -n "${HERMES_MODEL:-}" ]; then
        HERMES_ARGS="$HERMES_ARGS -m ${HERMES_MODEL}"
    fi
fi

MONITOR_LOOP_PID=""

if [ -f "$SCRIPT_DIR/hermes_monitor.md" ]; then
    bash -c '
        SCRIPT_DIR="'"$SCRIPT_DIR"'"
        WORK_DIR="'"$WORK_DIR"'"
        HERMES_ARGS="'"$HERMES_ARGS"'"
        INTERVAL=10
        HERMES_SESSION=""

        # ── 预热: 不等 claude 日志，先把 hermes 会话建好 ──
        # 首次 hermes chat 是冷启动 (CLI 初始化 + skill 预载 + 建会话 + 首次 LLM 往返，
        # 实测数十秒)，等日志触发再建会话会让监督姗姗来迟 (快题可能整场无监督)。
        # 预热只做: 读指令、建会话、回复就绪，不写任何文件；之后的触发全部复用会话。
        echo "=== [$(date "+%H:%M:%S")] Hermes 预热 (建会话，等解题日志) ===" >> "$WORK_DIR/hermes.log"
        touch "$WORK_DIR/.hermes_busy"
        WARM=$(hermes chat -q "你是 CTF 监督者。新挑战开始前的预热: 请读 $SCRIPT_DIR/hermes_monitor.md 了解你的职责并待命。现在还没有任何解题日志，不要读 agent.log、不要写任何文件，读完指令回复\"预热就绪\"即可。" $HERMES_ARGS \
            -t terminal,file,web,search,skills \
            -s ctf-supervisor-knowledge \
            --quiet 2>&1) || true
        HERMES_SESSION=$(echo "$WARM" | sed -n "s/.*session_id:[[:space:]]*\([^[:space:]]*\).*/\1/p" | head -1)
        echo "$WARM" >> "$WORK_DIR/hermes.log"
        rm -f "$WORK_DIR/.hermes_busy"
        echo "" >> "$WORK_DIR/hermes.log"

        while true; do
            OUTPUT=$(python3 "$SCRIPT_DIR/monitor.py" --work-dir "$WORK_DIR" 2>/dev/null)

            if [ -n "$OUTPUT" ]; then
                echo "=== [$(date "+%H:%M:%S")] Hermes agent 被触发 ===" >> "$WORK_DIR/hermes.log"
                touch "$WORK_DIR/.hermes_busy"

                if [ -z "$HERMES_SESSION" ]; then
                    # 第一次触发：新会话，给完整指令，捕获 session_id
                    # -s 预加载 ctf-supervisor-knowledge (SKILL.md 注入上下文, references 按需 skill_view)
                    RESP=$(hermes chat -q "你是 CTF 监督者。以下是 monitor.py 收集的解题 Agent 最新进展:
$OUTPUT

请读 $SCRIPT_DIR/hermes_monitor.md 获取详细指令，然后按指令执行。
执行完毕后回复简短摘要。" $HERMES_ARGS \
                        -t terminal,file,web,search,skills \
                        -s ctf-supervisor-knowledge \
                        --quiet 2>&1) || true
                    HERMES_SESSION=$(echo "$RESP" | sed -n "s/.*session_id:[[:space:]]*\([^[:space:]]*\).*/\1/p" | head -1)
                    echo "$RESP" >> "$WORK_DIR/hermes.log"
                else
                    # 后续触发：复用会话，简短 prompt 即可
                    hermes chat -q "解题 Agent 最新进展:
$OUTPUT

请按指令执行，回复简短摘要。" $HERMES_ARGS \
                        -r "$HERMES_SESSION" \
                        -t terminal,file,web,search,skills \
                        --quiet >> "$WORK_DIR/hermes.log" 2>&1 || true
                fi

                rm -f "$WORK_DIR/.hermes_busy"
                echo "" >> "$WORK_DIR/hermes.log"
            fi

            sleep "$INTERVAL"
        done
    ' &
    MONITOR_LOOP_PID=$!
    echo "[run.sh] Hermes monitor loop started (PID: $MONITOR_LOOP_PID, 10s interval${HERMES_ARGS:+, args:${HERMES_ARGS}})"
else
    echo "[run.sh] WARNING: hermes_monitor.md not found, skipping monitor"
fi

# ─── 清理函数 ───

cleanup() {
    echo ""
    echo "[run.sh] 清理中..."

    # hermes 生命周期收尾: 有在途 hermes 周期时等它写完 guidance/dead_ends
    # (最多 30s) 再停监控循环 —— 假 flag 通知、停滞拦截都在这类周期里落盘
    if [ -f "$WORK_DIR/.hermes_busy" ]; then
        echo "[run.sh] 等待在途 hermes 周期收尾 (最多 30s)..."
        for _ in $(seq 1 30); do
            [ ! -f "$WORK_DIR/.hermes_busy" ] && break
            sleep 1
        done
        rm -f "$WORK_DIR/.hermes_busy"
    fi

    if [ -n "$MONITOR_LOOP_PID" ]; then
        kill $MONITOR_LOOP_PID 2>/dev/null || true
        echo "[run.sh] Hermes monitor loop stopped"
    fi

    # claude 子进程兜底 (中断路径 trap 已杀过一次，这里双保险)
    if [ -n "${CLAUDE_PID:-}" ]; then
        kill "$CLAUDE_PID" 2>/dev/null || true
    fi
    echo "[run.sh] Done. Work dir: $WORK_DIR"
    echo "[run.sh] Log: $WORK_DIR/agent.log"
}
trap cleanup EXIT

# ─── 自动续跑循环 ───

INTERRUPTED=0
CLAUDE_PID=""
trap 'INTERRUPTED=1; if [ -n "${CLAUDE_PID:-}" ]; then kill "$CLAUDE_PID" 2>/dev/null || true; fi; echo "[run.sh] 收到中断信号，正在停止..."' SIGINT SIGTERM

# claude 公共参数
CLAUDE_ARGS=(--settings "$WORK_DIR/.claude_settings.json" --agents "$CLAUDE_AGENTS"
             --dangerously-skip-permissions --no-session-persistence
             --output-format stream-json --verbose)
if [ -f "$REPO_ROOT/solver/AGENT.md" ]; then
    CLAUDE_ARGS+=(--append-system-prompt-file "$REPO_ROOT/solver/AGENT.md")
fi
if [ -n "$CLAUDE_EFFORT" ]; then
    CLAUDE_ARGS+=(--effort "$CLAUDE_EFFORT")
fi

# 按题目类型构建解题 prompt
case "$CHALLENGE_TYPE" in
    web)
        AGENT_PROMPT="目标: $TARGET_URL
背景: $HINT

再读 board.md 了解当前 ideas 和 memory 状态。
再读 progress.md 了解当前进度。
然后继续解题。
每次工具调用后更新 progress.md。"
        ;;
    crypto|misc)
        AGENT_PROMPT="附件: $ATTACHMENT_IN_WORKDIR
背景: $HINT

这是一个 $CHALLENGE_TYPE 题目。附件已复制到工作目录。
再读 board.md 了解当前 ideas 和 memory 状态。
再读 progress.md 了解当前进度。
然后开始解题: 先解压/识别附件，分析文件内容，寻找 flag。
每次工具调用后更新 progress.md。"
        ;;
    binary)
        AGENT_PROMPT="目标: $TARGET_URL
附件: ${ATTACHMENT_IN_WORKDIR:-无}
背景: $HINT

这是一个二进制安全题目。远程服务: $TARGET_URL${ATTACHMENT_IN_WORKDIR:+，制品附件已复制到工作目录}。
再读 board.md 了解当前 ideas 和 memory 状态。
再读 progress.md 了解当前进度。
然后开始解题: 先逆向分析附件/探测远程服务协议，定位内存安全缺陷或逻辑漏洞，
编写 exploit (pwntools 可用) 从远程服务读取 flag。工具用法见 $REPO_ROOT/TOOLS.md。
每次工具调用后更新 progress.md。"
        ;;
esac

# 多 flag 题: prompt 声明总数量与续跑语义 (每轮都带上)
if [ "$FLAG_COUNT" -gt 1 ] 2>/dev/null; then
    AGENT_PROMPT="$AGENT_PROMPT

注意: 这是多 flag 题目，共 $FLAG_COUNT 个 flag，全部拿到才算通关。
progress.md 的 Flags Found 段里可能已有之前获得的 flag (已提交计分)，
不要重复提交它们，也不要重复攻击已拿过 flag 的入口，去寻找剩余的 flag
(通常意味着换攻击点/换入口/深入下一阶段)。每拿到一个新 flag 立即追加到
Flags Found 段 (一行一个)。"
fi

RETRY=0
while [ $RETRY -lt $MAX_RETRIES ] && [ $INTERRUPTED -eq 0 ]; do
    # master 调度模式: STOP 文件 = master 判定收工 (通关/终态/手动停)
    if [ "$MANAGED" = "1" ] && [ -f "$WORK_DIR/STOP" ]; then
        echo "[run.sh] STOP 文件出现 (master 已判定收工)，退出"
        break
    fi

    echo ""
    echo "=== claude round $((RETRY+1))/$MAX_RETRIES ==="
    echo "===== [round $((RETRY+1)) $(date -Iseconds)] =====" >> "$WORK_DIR/agent.log"

    cd "$WORK_DIR"
    # stream-json 事件流写 agent.log (hermes 监控数据源);
    # 过滤 thinking_tokens 流式计数噪音 (verbose 必开，否则 stream-json 拒绝运行)
    claude -p "$AGENT_PROMPT" "${CLAUDE_ARGS[@]}" \
        < /dev/null > >(grep --line-buffered -v '"thinking_tokens"' >> agent.log) 2>&1 &
    CLAUDE_PID=$!
    wait "$CLAUDE_PID" || true
    CLAUDE_PID=""

    # Ctrl+C 被按下 -> 不续跑，直接退出
    if [ $INTERRUPTED -eq 1 ]; then
        break
    fi

    if [ "$MANAGED" = "1" ]; then
        # 调度模式: flag 对错由平台/master 判定，这里不做"拿到即收工"推断
        # (假 flag 会被 master 当场清除并经 hermes 写 dead_ends，下一轮绕开)
        if [ -f "$WORK_DIR/STOP" ]; then
            echo "[run.sh] STOP 文件出现 (master 已判定收工)，退出"
            break
        fi
        RETRY=$((RETRY+1))
        if [ $RETRY -lt $MAX_RETRIES ] && [ $INTERRUPTED -eq 0 ]; then
            echo "[run.sh] round 结束，继续 (master 未叫停; $RETRY/$MAX_RETRIES)"
            sleep 3
        fi
        continue
    fi

    # ─── 独立模式: 检查 progress.md 的 Flags Found 段 (claude 主动声明的) ───
    # 注意: grep 无匹配时返回 1，不能用 set -e 让它退出整个脚本
    # 注意: 过滤 HTML 注释与进度笔记噪音 (与 master/challenge_state.py 的 _looks_like_flag 一致):
    #       含空格/中文/日期前缀、超长的行都是笔记，不算 flag
    FLAGS=$(awk '/^## *Flags Found/{f=1;next} /^##/{f=0} f' "$WORK_DIR/progress.md" \
        | grep -v '^(无)' | grep -v '^<!--' | grep -v '^$' \
        | grep -v ' ' \
        | LC_ALL=C grep -v '[^ -~]' \
        | awk 'length($0) <= 128 && $0 !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/' \
        | head -1 || true)
    # 多 flag 题: 统计已得 flag 数，拿满 FLAG_COUNT 个才算完成 (单 flag 行为不变)
    FLAGS_COUNT=$(awk '/^## *Flags Found/{f=1;next} /^##/{f=0} f' "$WORK_DIR/progress.md" \
        | grep -v '^(无)' | grep -v '^<!--' | grep -v '^$' \
        | grep -v ' ' \
        | LC_ALL=C grep -v '[^ -~]' \
        | awk 'length($0) <= 128 && $0 !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/' \
        | wc -l | tr -d ' ' || true)
    if [ -n "$FLAGS" ]; then
        if [ "$FLAGS_COUNT" -ge "$FLAG_COUNT" ] 2>/dev/null || [ "$FLAG_COUNT" = "1" ]; then
            echo ""
            echo "=== FLAG FOUND! (${FLAGS_COUNT}/${FLAG_COUNT} 全部拿到) ==="
            echo "$FLAGS"
            echo "=== Check agent.log for details ==="
            break
        fi
        echo ""
        echo "=== FLAG FOUND (${FLAGS_COUNT}/${FLAG_COUNT})，多 flag 未拿满，继续攻剩余 ==="
        # 不 break: 下一轮续跑继续找 (prompt 会带已得 flag 进度)
    fi

    # claude 正常退出但没 flag，继续
    RETRY=$((RETRY+1))
    if [ $RETRY -lt $MAX_RETRIES ] && [ $INTERRUPTED -eq 0 ]; then
        echo "[run.sh] No flag yet, retrying in 3s... ($RETRY/$MAX_RETRIES)"
        sleep 3
    fi
done

if [ $RETRY -ge $MAX_RETRIES ] && [ $INTERRUPTED -eq 0 ]; then
    echo ""
    if [ "$MANAGED" = "1" ]; then
        echo "[run.sh] 达到最大轮次 ($MAX_RETRIES)，退出 (由 master 判定后续)"
    else
        echo "[run.sh] 达到最大重试次数 ($MAX_RETRIES)，退出"
    fi
fi
