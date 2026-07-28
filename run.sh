#!/bin/bash
#
# run.sh -- CTF Agent 启动脚本
#
# 用法: ./run.sh <target_url> <background_hint>
# 示例: ./run.sh "http://target:8080" "这是XX系统，可能存在SQL注入"
#
# 功能:
#   1. 创建挑战工作目录 + 初始化文件
#   2. 启动 branch.py daemon (subagent 管理)
#   3. 启动 Hermes 监控 (background loop + agent, 10s 轮询)
#   4. 自动续跑 Codex (最多 10 轮)
#   5. 退出时清理 daemon + Hermes 监控

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── 参数检查 ───

if [ $# -lt 1 ]; then
    echo "用法: $0 <target_url> [background_hint]"
    echo "示例: $0 \"http://target:8080\" \"这是XX系统，可能存在SQL注入\""
    exit 1
fi

TARGET_URL="$1"
HINT="${2:-}"
MAX_RETRIES=10

# 工作目录: challenges/manual_<host>_<port>
WORK_DIR_NAME="manual_$(echo "$TARGET_URL" | sed 's|https\?://||;s|[:/]|_|g')"
WORK_DIR="$SCRIPT_DIR/challenges/$WORK_DIR_NAME"

echo "=== CTF Agent 启动 ==="
echo "Target: $TARGET_URL"
echo "Work dir: $WORK_DIR"
echo ""

# ─── 初始化工作目录 ───

mkdir -p "$WORK_DIR/poc_scripts"

# progress.md
cat > "$WORK_DIR/progress.md" << EOF
## Target
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

echo "[run.sh] 工作目录初始化完成"

# ─── 启动 branch daemon ───

python3 "$SCRIPT_DIR/branch.py" daemon --work-dir "$WORK_DIR" &
BRANCH_DAEMON_PID=$!
echo "[run.sh] Branch daemon started (PID: $BRANCH_DAEMON_PID)"

# 等待 daemon 就绪 (socket 出现)
for i in $(seq 1 10); do
    if [ -S "$WORK_DIR/branch.sock" ]; then
        echo "[run.sh] Branch daemon ready"
        break
    fi
    sleep 0.3
done

if [ ! -S "$WORK_DIR/branch.sock" ]; then
    echo "[run.sh] ERROR: Branch daemon failed to start"
    kill $BRANCH_DAEMON_PID 2>/dev/null || true
    exit 1
fi

# ─── 启动 Hermes 监控 (background loop) ───
# monitor.py 每 10s tail codex.log，有新日志增量时调 hermes agent
# Hermes agent 看到日志增量后主动判断该不该给建议/搜索/拦
# 不依赖 gateway/cronjob，直接后台 bash 循环

MONITOR_LOOP_PID=""

if [ -f "$SCRIPT_DIR/hermes_monitor.md" ]; then
    # 启动后台监控循环
    bash -c '
        SCRIPT_DIR="'"$SCRIPT_DIR"'"
        WORK_DIR="'"$WORK_DIR"'"
        INTERVAL=10

        while true; do
            # 运行 monitor.py，捕获输出 (日志增量 + progress 状态)
            OUTPUT=$(python3 "$SCRIPT_DIR/monitor.py" --work-dir "$WORK_DIR" 2>/dev/null)

            # 有输出 -> 调 hermes agent (Hermes 的眼睛看到新进展)
            if [ -n "$OUTPUT" ]; then
                hermes chat -q "你是 CTF 监督者。以下是 monitor.py 收集的 Codex 最新进展:
$OUTPUT

请读 $SCRIPT_DIR/hermes_monitor.md 获取详细指令，然后按指令执行。
执行完毕后回复简短摘要。" \
                    -t terminal,file,web,search \
                    --quiet 2>/dev/null || true
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

    # 停止 Hermes 监控循环
    if [ -n "$MONITOR_LOOP_PID" ]; then
        kill $MONITOR_LOOP_PID 2>/dev/null || true
        echo "[run.sh] Hermes monitor loop stopped"
    fi

    # 停止 branch daemon
    python3 "$SCRIPT_DIR/branch.py" shutdown --work-dir "$WORK_DIR" 2>/dev/null || true
    kill $BRANCH_DAEMON_PID 2>/dev/null || true
    echo "[run.sh] Done. Work dir: $WORK_DIR"
    echo "[run.sh] Log: $WORK_DIR/codex.log"
}
trap cleanup EXIT

# ─── 自动续跑循环 ───

INTERRUPTED=0
trap 'INTERRUPTED=1; echo "[run.sh] 收到中断信号，正在停止..."' SIGINT SIGTERM

RETRY=0
while [ $RETRY -lt $MAX_RETRIES ] && [ $INTERRUPTED -eq 0 ]; do
    echo ""
    echo "=== Codex round $((RETRY+1))/$MAX_RETRIES ==="

    cd "$WORK_DIR"
    codex exec --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust --ignore-rules --disable guardian_approval -c model_reasoning_effort="medium" "目标: $TARGET_URL
背景: $HINT

先读 board.md 了解当前 ideas 和 memory 状态。
再读 progress.md 了解当前进度。
然后继续解题。
每次工具调用后更新 progress.md。" \
        > codex.log 2>&1 || true

    # Ctrl+C 被按下 -> 不续跑，直接退出
    if [ $INTERRUPTED -eq 1 ]; then
        break
    fi

    # 检查 progress.md 的 Flags Found 段 (Codex 主动声明的，不碰 codex.log)
    FLAGS=$(awk '/^## *Flags Found/{f=1;next} /^##/{f=0} f' "$WORK_DIR/progress.md" | grep -v '^(无)' | grep -v '^$' | head -1)
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

if [ $RETRY -ge $MAX_RETRIES ]; then
    echo ""
    echo "[run.sh] 达到最大重试次数 ($MAX_RETRIES)，退出"
fi
