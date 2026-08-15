#!/bin/bash
#
# run.sh -- CTF Agent 启动脚本
#
# 用法:
#   ./run.sh --type web --url "http://target:8080" --hint "SQL注入"
#   ./run.sh --type crypto --attachment "/path/to/challenge.zip" --hint "RSA"
#   ./run.sh --type misc --attachment "/path/to/file.zip" --hint "隐写"
#   ./run.sh --type binary --url "http://target:9999" [--attachment "/path/to/binary"] --hint "栈溢出"
#
# 功能:
#   1. 创建挑战工作目录 + 初始化文件
#   2. 启动 branch.py daemon (subagent 管理)
#   3. 启动 Hermes 监控 (background loop, 输出写 hermes.log)
#   4. 自动续跑 Codex (最多 10 轮)
#   5. 退出时清理 daemon + Hermes 监控

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ─── 参数解析 ───

CHALLENGE_TYPE=""
TARGET_URL=""
ATTACHMENT=""
HINT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --type)       CHALLENGE_TYPE="$2"; shift 2 ;;
        --url)        TARGET_URL="$2"; shift 2 ;;
        --attachment) ATTACHMENT="$2"; shift 2 ;;
        --hint)       HINT="$2"; shift 2 ;;
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
        # 用 MD5 短哈希避免 URL 带路径时 socket 路径超长 (AF_UNIX 限制 108)
        SHORT_HASH=$(printf '%s' "$TARGET_URL" | md5sum | cut -c1-12)
        WORK_DIR_NAME="manual_web_${SHORT_HASH}"
        ;;
    crypto|misc)
        if [ -z "$ATTACHMENT" ]; then echo "$CHALLENGE_TYPE 类型需要 --attachment"; exit 1; fi
        if [ ! -f "$ATTACHMENT" ]; then echo "附件不存在: $ATTACHMENT"; exit 1; fi
        # 复制附件到工作目录，用短哈希避免 socket 路径超长
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

MAX_RETRIES=10
WORK_DIR="$REPO_ROOT/challenges/$WORK_DIR_NAME"
BRANCH_SOCKET=$(python3 "$SCRIPT_DIR/branch.py" socket-path --work-dir "$WORK_DIR")

echo "=== CTF Agent 启动 ==="
echo "Type: $CHALLENGE_TYPE"
case "$CHALLENGE_TYPE" in
    web)          echo "Target: $TARGET_URL" ;;
    crypto|misc)  echo "Attachment: $ATTACHMENT" ;;
    binary)       echo "Target: $TARGET_URL"; [ -n "$ATTACHMENT" ] && echo "Attachment: $ATTACHMENT" ;;
esac
echo "Work dir: $WORK_DIR"
echo "Branch socket: $BRANCH_SOCKET"
echo ""

# ─── 初始化工作目录 ───

# 清理上一次运行的残留状态（同一 URL 会复用工作目录）
rm -f "$WORK_DIR/branch_state.json" "$WORK_DIR/branch.sock" "$BRANCH_SOCKET"
rm -f "$WORK_DIR/branch_result_"*.md
rm -f "$WORK_DIR/codex.log" "$WORK_DIR/hermes.log" "$WORK_DIR/monitor_state.json"

mkdir -p "$WORK_DIR/poc_scripts"

# crypto/misc/binary: 复制附件到工作目录
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
1. 有附件先 file/strings/逆向分析制品
2. 探测远程服务协议
3. 寻找内存安全缺陷/逻辑漏洞，构造 exploit

## Key Artifacts

## Flags Found
(无)
EOF
        ;;
esac

# board.md (空看板)
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
touch "$WORK_DIR/hermes.log"

echo "[run.sh] 工作目录初始化完成"

# ─── 启动 branch daemon ───

python3 "$SCRIPT_DIR/branch.py" daemon --work-dir "$WORK_DIR" &
BRANCH_DAEMON_PID=$!
echo "[run.sh] Branch daemon started (PID: $BRANCH_DAEMON_PID)"

# 等待 daemon 就绪 (socket 出现)
for i in $(seq 1 10); do
    if [ -S "$BRANCH_SOCKET" ]; then
        echo "[run.sh] Branch daemon ready"
        break
    fi
    sleep 0.3
done

if [ ! -S "$BRANCH_SOCKET" ]; then
    echo "[run.sh] ERROR: Branch daemon failed to start ($BRANCH_SOCKET)"
    kill $BRANCH_DAEMON_PID 2>/dev/null || true
    exit 1
fi

# ─── 启动 Hermes 监控 (background loop, 输出写 hermes.log) ───
# monitor.py 每 10s tail codex.log，有新日志增量时调 hermes agent
# Hermes agent 的输出写入 hermes.log (供 dashboard 实时展示)

MONITOR_LOOP_PID=""

if [ -f "$SCRIPT_DIR/hermes_monitor.md" ]; then
    bash -c '
        SCRIPT_DIR="'"$SCRIPT_DIR"'"
        WORK_DIR="'"$WORK_DIR"'"
        INTERVAL=10
        HERMES_SESSION=""

        while true; do
            OUTPUT=$(python3 "$SCRIPT_DIR/monitor.py" --work-dir "$WORK_DIR" 2>/dev/null)

            if [ -n "$OUTPUT" ]; then
                echo "=== [$(date "+%H:%M:%S")] Hermes agent 被触发 ===" >> "$WORK_DIR/hermes.log"

                if [ -z "$HERMES_SESSION" ]; then
                    # 第一次触发：新会话，给完整指令，捕获 session_id
                    RESP=$(hermes chat -q "你是 CTF 监督者。以下是 monitor.py 收集的 Codex 最新进展:
$OUTPUT

请读 $SCRIPT_DIR/hermes_monitor.md 获取详细指令，然后按指令执行。
执行完毕后回复简短摘要。" \
                        -t terminal,file,web,search \
                        --quiet 2>&1) || true
                    HERMES_SESSION=$(echo "$RESP" | grep -o 'session_id:[[:space:]]*[^[:space:]]*' | sed -E 's/session_id:[[:space:]]*//' | head -1)
                    echo "$RESP" >> "$WORK_DIR/hermes.log"
                else
                    # 后续触发：复用会话，简短 prompt 即可
                    hermes chat -q "Codex 最新进展:
$OUTPUT

请按指令执行，回复简短摘要。" \
                        -r "$HERMES_SESSION" \
                        -t terminal,file,web,search \
                        --quiet >> "$WORK_DIR/hermes.log" 2>&1 || true
                fi

                echo "" >> "$WORK_DIR/hermes.log"
            fi

            sleep "$INTERVAL"
        done
    ' &
    MONITOR_LOOP_PID=$!
    echo "[run.sh] Hermes monitor loop started (PID: $MONITOR_LOOP_PID, 10s interval)"
else
    echo "[run.sh] WARNING: hermes_monitor.md not found, skipping monitor"
fi

# ─── 清理函数 ───

cleanup() {
    echo ""
    echo "[run.sh] 清理中..."

    if [ -n "$MONITOR_LOOP_PID" ]; then
        kill $MONITOR_LOOP_PID 2>/dev/null || true
        echo "[run.sh] Hermes monitor loop stopped"
    fi

    python3 "$SCRIPT_DIR/branch.py" shutdown --work-dir "$WORK_DIR" 2>/dev/null || true
    kill $BRANCH_DAEMON_PID 2>/dev/null || true
    echo "[run.sh] Done. Work dir: $WORK_DIR"
    echo "[run.sh] Log: $WORK_DIR/codex.log"
}
trap cleanup EXIT

# ─── 自动续跑循环 ───

INTERRUPTED=0
trap 'INTERRUPTED=1; echo "[run.sh] 收到中断信号，正在停止..."' SIGINT SIGTERM

# 按题目类型构建 Codex prompt
case "$CHALLENGE_TYPE" in
    web)
        CODEX_PROMPT="目标: $TARGET_URL
背景: $HINT

先读 $REPO_ROOT/strategies/web.md 了解 Web 题攻击流程。
再读 board.md 了解当前 ideas 和 memory 状态。
再读 progress.md 了解当前进度。
然后继续解题。
每次工具调用后更新 progress.md。"
        ;;
    crypto|misc)
        CODEX_PROMPT="附件: $ATTACHMENT_IN_WORKDIR
背景: $HINT

这是一个 $CHALLENGE_TYPE 题目。附件已复制到工作目录。
先读 $REPO_ROOT/strategies/$CHALLENGE_TYPE.md 了解 $CHALLENGE_TYPE 题攻击流程。
再读 board.md 了解当前 ideas 和 memory 状态。
再读 progress.md 了解当前进度。
然后开始解题: 先解压/识别附件，分析文件内容，寻找 flag。
每次工具调用后更新 progress.md。"
        ;;
    binary)
        CODEX_PROMPT="目标: $TARGET_URL
附件: ${ATTACHMENT_IN_WORKDIR:-无}
背景: $HINT

这是一个二进制安全题目。远程服务: $TARGET_URL${ATTACHMENT_IN_WORKDIR:+，制品附件已复制到工作目录}。
先读 $REPO_ROOT/strategies/binary.md 了解二进制题攻击流程。
再读 board.md 了解当前 ideas 和 memory 状态。
再读 progress.md 了解当前进度。
然后开始解题: 先逆向分析附件/探测远程服务协议，定位内存安全缺陷或逻辑漏洞，
编写 exploit (pwntools 可用) 从远程服务读取 flag。
每次工具调用后更新 progress.md。"
        ;;
esac

RETRY=0
while [ $RETRY -lt $MAX_RETRIES ] && [ $INTERRUPTED -eq 0 ]; do
    echo ""
    echo "=== Codex round $((RETRY+1))/$MAX_RETRIES ==="

    cd "$WORK_DIR"
    codex exec --profile ctf --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust \
      --ignore-rules --disable guardian_approval -c model_reasoning_effort="xhigh" \
      "$CODEX_PROMPT" \
        < /dev/null > codex.log 2>&1 || true

    # Ctrl+C 被按下 -> 不续跑，直接退出
    if [ $INTERRUPTED -eq 1 ]; then
        break
    fi

    # 检查 progress.md 的 Flags Found 段 (Codex 主动声明的，不碰 codex.log)
    # 注意: grep 无匹配时返回 1，不能用 set -e 让它退出整个脚本
    # 注意: 过滤 HTML 注释 (Codex/branch 会写 <!-- --> 进度笔记到 Flags Found 段)
    # 注意: 模型偶尔会把进度笔记写进该段 (如 "- 2026-08-15: 已读取xx，准备继续侦察")，
    #       所以加"像 flag"过滤 (与 master/challenge_state.py 的 _looks_like_flag 一致):
    #       含空格/中文/日期前缀、超长的行都是笔记，不算 flag
    FLAGS=$(awk '/^## *Flags Found/{f=1;next} /^##/{f=0} f' "$WORK_DIR/progress.md" \
        | grep -v '^(无)' | grep -v '^<!--' | grep -v '^$' \
        | grep -v ' ' \
        | grep -v '[一-鿿]' \
        | awk 'length($0) <= 128 && $0 !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/' \
        | head -1 || true)
    if [ -n "$FLAGS" ]; then
        echo ""
        echo "=== FLAG FOUND! ==="
        echo "$FLAGS"
        echo "=== Check codex.log for details ==="
        break
    fi

    # Codex 正常退出但没 flag，继续
    RETRY=$((RETRY+1))
    if [ $RETRY -lt $MAX_RETRIES ] && [ $INTERRUPTED -eq 0 ]; then
        echo "[run.sh] No flag yet, retrying in 3s... ($RETRY/$MAX_RETRIES)"
        sleep 3
    fi
done

if [ $RETRY -ge $MAX_RETRIES ] && [ $INTERRUPTED -eq 0 ]; then
    echo ""
    echo "[run.sh] 达到最大重试次数 ($MAX_RETRIES)，退出"
fi
