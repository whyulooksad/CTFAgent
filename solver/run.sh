#!/bin/bash
#
# run.sh -- CTF Agent 启动脚本
#
# 用法:
#   ./run.sh --type web --url "http://target:8080" --hint "SQL注入"
#   ./run.sh --type crypto --attachment "/path/to/challenge.zip" --hint "RSA"
#   ./run.sh --type misc --attachment "/path/to/file.zip" --hint "隐写"
#
# 功能:
#   1. 创建挑战工作目录 + 初始化文件
#   2. 启动 branch.py daemon (subagent 管理)
#   3. 启动 Hermes 监控 (background loop, 输出写 hermes.log)
#   4. 自动续跑 Codex (最多 10 轮)
#   5. 退出时清理 daemon + Hermes 监控

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"   # 仓库根 (challenges/ 所在)

# ─── 参数解析 ───

CHALLENGE_TYPE=""
TARGET_URL=""
ATTACHMENT=""
HINT=""
FLAG_COUNT=1
CHALLENGE_ID=""

while [ $# -gt 0 ]; do
    case "$1" in
        --type)       CHALLENGE_TYPE="$2"; shift 2 ;;
        --url)        TARGET_URL="$2"; shift 2 ;;
        --attachment) ATTACHMENT="$2"; shift 2 ;;
        --hint)       HINT="$2"; shift 2 ;;
        --flag-count) FLAG_COUNT="$2"; shift 2 ;;
        --challenge-id) CHALLENGE_ID="$2"; shift 2 ;;
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
        # 优先用 challenge-id 哈希: 平台并发实例 IP 会复用，按 url 命名不同题会撞目录
        if [ -n "$CHALLENGE_ID" ]; then
            SHORT_HASH=$(printf '%s' "$CHALLENGE_ID" | md5sum | cut -c1-12)
        else
            SHORT_HASH=$(printf '%s' "$TARGET_URL" | md5sum | cut -c1-12)
        fi
        WORK_DIR_NAME="manual_web_${SHORT_HASH}"
        ;;
    crypto|misc)
        if [ -z "$ATTACHMENT" ]; then echo "$CHALLENGE_TYPE 类型需要 --attachment"; exit 1; fi
        if [ ! -f "$ATTACHMENT" ]; then echo "附件不存在: $ATTACHMENT"; exit 1; fi
        # 复制附件到工作目录，用短哈希避免 socket 路径超长
        ATTACHMENT_NAME=$(basename "$ATTACHMENT")
        if [ -n "$CHALLENGE_ID" ]; then
            SHORT_HASH=$(printf '%s' "$CHALLENGE_ID" | md5sum | cut -c1-12)
        else
            SHORT_HASH=$(printf '%s' "$ATTACHMENT" | md5sum | cut -c1-12)
        fi
        WORK_DIR_NAME="manual_${CHALLENGE_TYPE}_${SHORT_HASH}"
        ;;
    binary)
        # 二进制题: 远程服务 (URL) + 可选附件 (二进制制品/固件)
        if [ -z "$TARGET_URL" ]; then echo "binary 类型需要 --url (远程服务地址)"; exit 1; fi
        if [ -n "$ATTACHMENT" ] && [ ! -f "$ATTACHMENT" ]; then
            echo "附件不存在: $ATTACHMENT"; exit 1
        fi
        if [ -n "$CHALLENGE_ID" ]; then
            SHORT_HASH=$(printf '%s' "$CHALLENGE_ID" | md5sum | cut -c1-12)
        else
            SHORT_HASH=$(printf '%s' "$TARGET_URL" | md5sum | cut -c1-12)
        fi
        WORK_DIR_NAME="manual_binary_${SHORT_HASH}"
        ;;
    *)
        echo "未知题目类型: $CHALLENGE_TYPE (支持: web, crypto, misc, binary)"
        exit 1
        ;;
esac

# 多 flag 题需要更多轮次: 基线 10 轮 × flag 数 (每 flag 可能要好几轮 codex)
# 单 flag 题保持 10 轮。避免 b 系列多 flag 题 1/4 就被轮次耗尽掐断。
MAX_RETRIES=$(( 10 * FLAG_COUNT ))
WORK_DIR="$REPO_ROOT/challenges/$WORK_DIR_NAME"

echo "=== CTF Agent 启动 ==="
echo "Type: $CHALLENGE_TYPE"
case "$CHALLENGE_TYPE" in
    web)        echo "Target: $TARGET_URL" ;;
    crypto|misc) echo "Attachment: $ATTACHMENT" ;;
esac
echo "Work dir: $WORK_DIR"
echo ""

# ─── 初始化工作目录 ───

# 续跑判定: progress.md 存在且含 Target 段 = 上一轮遗留（容器重启/重试）
# 续跑时保留 progress.md/board.md 上下文，仅全新目录才初始化模板
IS_RESUME=0
if [ -s "$WORK_DIR/progress.md" ] && grep -q "^## Target" "$WORK_DIR/progress.md" 2>/dev/null; then
    IS_RESUME=1
fi

# 清理上一次运行的残留状态（同一 URL 会复用工作目录）
# branch.sock 实际在 /tmp/ctf-agent-<uid>/ 短路径下 (AF_UNIX 108 限制)，由 socket-path 查询
BRANCH_SOCKET=$(python3 "$SCRIPT_DIR/branch.py" socket-path --work-dir "$WORK_DIR")
rm -f "$BRANCH_SOCKET"
# codex_round_*.log 是本次运行期的按轮归档，重启即重新编号 (续跑轮次从 1 重数，
# 保留旧 round 文件会与新轮次追加混合；上轮诊断看保留的 codex.log)
rm -f "$WORK_DIR/codex_round_"*.log
# 续跑保留 branch_state.json + branch_result_*.md: branch daemon 启动时 _restore_state
# 恢复 subagents (counter 一并恢复)，Codex 可继续读上轮 subagent 结果 (branch.py results)
# —— 否则上轮 subagent 找到的 flag 会随重启永久丢失 (b-02 真实案例)
if [ "$IS_RESUME" = "0" ]; then
    rm -f "$WORK_DIR/branch_state.json"
    rm -f "$WORK_DIR/branch_result_"*.md
fi
# 全新目录才删日志与提交记录; 续跑保留 hermes.log（Hermes 历史）+ codex.log 历史
# + submit_results.jsonl（防重复提交/重复攻击）
if [ "$IS_RESUME" = "0" ]; then
    rm -f "$WORK_DIR/codex.log"
    rm -f "$WORK_DIR/hermes.log" "$WORK_DIR/monitor_state.json"
    rm -f "$WORK_DIR/submit_result.json" "$WORK_DIR/submit_results.jsonl"
fi

mkdir -p "$WORK_DIR/poc_scripts"

# AGENTS.md 副本: Codex 从 work_dir (cwd) 加载，solver/AGENTS.md 不在向上查找路径上
# (work_dir=challenges/<name>/ -> challenges/ -> 根，均无 AGENTS.md)，必须复制到 cwd
cp "$SCRIPT_DIR/AGENTS.md" "$WORK_DIR/AGENTS.md"
# branch.py 副本: AGENTS.md 的 subagent 规则用裸 `python3 branch.py ...` (cwd=work_dir)，
# 脚本本体在 solver/，不复制则 Codex 找不到、subagent 能力失效
cp "$SCRIPT_DIR/branch.py" "$WORK_DIR/branch.py"

# crypto/misc: 复制附件到工作目录
if [ -n "$ATTACHMENT" ]; then
    cp "$ATTACHMENT" "$WORK_DIR/"
    ATTACHMENT_IN_WORKDIR="$WORK_DIR/$(basename "$ATTACHMENT")"
fi

# progress.md / board.md -- 全新目录才初始化模板；续跑保留上轮上下文
if [ "$IS_RESUME" = "0" ]; then
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

else
    echo "[run.sh] 续跑模式: 保留 progress.md/board.md 上下文 ($WORK_DIR)"
    # 保险: 平台题重试会重开靶机拿新 URL，续跑时同步更新 progress.md 的
    # Target URL 行（只改 URL 行，保留其余内容），避免 Codex 读到过期 URL 混淆
    # (a-03 M19 真实案例)。crypto/misc 无 URL 参数时自动跳过。
    if [ -n "$TARGET_URL" ]; then
        python3 - "$WORK_DIR" "$TARGET_URL" << 'PYEOF'
import os
import sys

work_dir, url = sys.argv[1], sys.argv[2]
p = os.path.join(work_dir, "progress.md")
try:
    lines = open(p, encoding="utf-8").read().splitlines(keepends=True)
    out, changed, in_target = [], False, False
    for ln in lines:
        if ln.startswith("## Target"):
            in_target = True
        elif ln.startswith("## ") and not ln.startswith("## Target"):
            in_target = False
        if in_target and ln.startswith("- URL:"):
            out.append(f"- URL: {url}\n")
            changed = True
            continue
        out.append(ln)
    if changed:
        tmp = p + ".tmp"
        open(tmp, "w", encoding="utf-8").writelines(out)
        os.replace(tmp, p)
        print(f"[run.sh] progress.md Target URL 已更新: {url}")
except Exception as e:
    print(f"[run.sh] 更新 Target URL 失败: {e}")
PYEOF
    fi
fi

# 空文件
touch "$WORK_DIR/guidance.md"
touch "$WORK_DIR/dead_ends.md"
touch "$WORK_DIR/human_guidance.md"
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
    echo "[run.sh] ERROR: Branch daemon failed to start"
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
        # 复用 warmup 建立的 session（.hermes_session 由 warmup 写入），避免双 session 竞态
        HERMES_SESSION=""
        if [ -f "$WORK_DIR/.hermes_session" ]; then
            HERMES_SESSION=$(cat "$WORK_DIR/.hermes_session" 2>/dev/null || true)
        fi

        while true; do
            OUTPUT=$(python3 "$SCRIPT_DIR/monitor.py" --work-dir "$WORK_DIR" 2>/dev/null)

            if [ -n "$OUTPUT" ]; then
                echo "=== [$(date "+%H:%M:%S")] Hermes agent 被触发 ===" >> "$WORK_DIR/hermes.log"

                if [ -z "$HERMES_SESSION" ]; then
                    # warmup 尚未建立 session：等它完成（最多 360s，hermes chat 慢）
                    # 成功 -> .hermes_session；失败 -> .hermes_warmup_failed
                    for i in $(seq 1 60); do
                        if [ -f "$WORK_DIR/.hermes_session" ]; then
                            HERMES_SESSION=$(cat "$WORK_DIR/.hermes_session" 2>/dev/null || true)
                            break
                        fi
                        if [ -f "$WORK_DIR/.hermes_warmup_failed" ]; then
                            break
                        fi
                        sleep 6
                    done
                fi

                if [ -z "$HERMES_SESSION" ]; then
                    # warmup 超时/失败：自己建新会话，给完整指令，捕获 session_id
                    # -s 预加载 ctf-web (监督速查路由；其他方向按需 skill_view)
                    RESP=$(timeout 300 hermes chat -q "你是 CTF 监督者。以下是 monitor.py 收集的 Codex 最新进展:
$OUTPUT

请读 $SCRIPT_DIR/hermes_monitor.md 获取详细指令，然后按指令执行。
执行完毕后回复简短摘要。" \
                        -t terminal,file,web,search,skills \
                        -s ctf-web \
                        --quiet 2>&1) || true
                    HERMES_SESSION=$(echo "$RESP" | grep -oP "session_id:\s*\K[^\s]+" | head -1)
                    if [ -n "$HERMES_SESSION" ]; then
                        printf "%s" "$HERMES_SESSION" > "$WORK_DIR/.hermes_session"
                    fi
                    echo "$RESP" >> "$WORK_DIR/hermes.log"
                else
                    # 后续触发：复用会话，简短 prompt 即可
                    # timeout 300: hermes chat 挂起不能阻塞 monitor 循环
                    timeout 300 hermes chat -q "Codex 最新进展:
$OUTPUT

请按指令执行，回复简短摘要。" \
                        -r "$HERMES_SESSION" \
                        -t terminal,file,web,search,skills \
                        --quiet >> "$WORK_DIR/hermes.log" 2>&1 || true
                fi

                echo "" >> "$WORK_DIR/hermes.log"
            fi

            sleep "$INTERVAL"
        done
    ' &
    MONITOR_LOOP_PID=$!
    echo "[run.sh] Hermes monitor loop started (PID: $MONITOR_LOOP_PID, 10s interval)"

    # ─── Hermes 预热: 初始化/续跑 board.md (不等 codex 日志, 与 codex 第一轮并行) ───
    # 解决 "codex 解完题 hermes 首次初始化(~3min)还没完成" 的时序问题
    # 后台跑不阻塞主流程; 预热只初始化 board.md, 动态监督仍由 monitor 循环驱动
    # 续跑模式: board.md 已有上下文 → 禁止重建，改为追加维护
    if [ "$IS_RESUME" = "1" ]; then
        WARMUP_MSG="你是 CTF 监督者。续跑模式：请读 $SCRIPT_DIR/hermes_monitor.md 了解职责，\
读 board.md（已有完整上下文，禁止重建/覆盖，只允许追加维护）和 progress.md，\
确认当前进展与失败记录后继续监督。回复简短摘要。"
    else
        WARMUP_MSG="你是 CTF 监督者。新任务开始，请读 $SCRIPT_DIR/hermes_monitor.md 了解职责，\
读 progress.md（如存在）和题目背景，初始化 board.md 记录目标/URL/已知信息。回复简短摘要。"
    fi
    (
        # 预热建立 Hermes session，session_id 写 .hermes_session 供 monitor loop 复用
        # （避免 warmup 与 monitor 各建一个 session 并发写 board.md 的竞态）
        # 失败写 .hermes_warmup_failed 标记，让 monitor/board 等待方知道可自建/继续
        # timeout 300: hermes chat 挂起时不能无限等
        rm -f "$WORK_DIR/.hermes_warmup_failed"
        RESP=$(timeout 300 hermes chat -q "$WARMUP_MSG" \
            -t terminal,file,web,search,skills \
            -s ctf-web \
            --quiet 2>&1) || true
        echo "$RESP" >> "$WORK_DIR/hermes.log"
        SID=$(echo "$RESP" | grep -oP "session_id:\s*\K[^\s]+" | head -1)
        if [ -n "$SID" ]; then
            printf '%s' "$SID" > "$WORK_DIR/.hermes_session"
        else
            printf 'warmup failed' > "$WORK_DIR/.hermes_warmup_failed"
        fi
    ) &
    HERMES_WARMUP_PID=$!
    echo "[run.sh] Hermes warmup started (PID: $HERMES_WARMUP_PID)"
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
    if [ -n "${HERMES_WARMUP_PID:-}" ]; then
        kill $HERMES_WARMUP_PID 2>/dev/null || true
        echo "[run.sh] Hermes warmup stopped"
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

【第一步必做】先 cat board.md 和 progress.md 恢复上下文（board.md 含已知结论/失败记录/当前方向，progress.md 含当前进度）。
未读完这两个文件前，禁止执行任何攻击命令。读完后再继续解题。
每次工具调用后更新 progress.md。"
        ;;
    crypto|misc)
        CODEX_PROMPT="附件: $ATTACHMENT_IN_WORKDIR
背景: $HINT

这是一个 $CHALLENGE_TYPE 题目。附件已复制到工作目录。
【第一步必做】先 cat board.md 和 progress.md 恢复上下文（board.md 含已知结论/失败记录/当前方向，progress.md 含当前进度）。
未读完这两个文件前，禁止执行任何攻击命令。读完后再开始解题: 先解压/识别附件，分析文件内容，寻找 flag。
每次工具调用后更新 progress.md。"
        ;;
    binary)
        CODEX_PROMPT="目标: $TARGET_URL
附件: ${ATTACHMENT_IN_WORKDIR:-无}
背景: $HINT

这是一个二进制安全题目。远程服务: $TARGET_URL${ATTACHMENT_IN_WORKDIR:+，制品附件已复制到工作目录}。
【第一步必做】先 cat board.md 和 progress.md 恢复上下文（board.md 含已知结论/失败记录/当前方向，progress.md 含当前进度）。
未读完这两个文件前，禁止执行任何攻击命令。读完后再开始解题: 先逆向分析附件/探测远程服务协议，定位内存安全缺陷或逻辑漏洞，
编写 exploit (pwntools 可用) 从远程服务读取 flag。工具用法见 $SCRIPT_DIR/TOOLS.md。
每次工具调用后更新 progress.md。"
        ;;
esac

# 并行试探硬指令 (deepseek 等模型不主动用 subagent，每轮 prompt 都强调)
CODEX_PROMPT="$CODEX_PROMPT

重要 (硬性要求): 遇到分岔路口 —— 即 2 个及以上可行攻击方向需要验证时 —— 必须用 branch.py spawn 并行试探，
不要在主会话里串行一个个试。用法:
  python3 branch.py spawn --work-dir . --name \"方向名\" --prompt \"...\"
  python3 branch.py status --work-dir .          # 查看所有 subagent 状态
  python3 branch.py results --work-dir . <id>    # 读已完成结果
  python3 branch.py kill --work-dir . <id>       # 终止不需要的方向
spawn 后继续你的主攻方向，定期 status 查状态；某方向 FEASIBLE 就深入、INFEASIBLE 就 kill。

禁止联网搜索: 不要使用 web search / 上网搜答案 / 搜 CVE 或 WP。所有情报必须通过
对靶机的实际探测获取 (curl/nmap/ffuf/python3 等)。这是硬性要求。"

# 多 flag 题: prompt 声明总数量与续跑语义 (每轮 codex 都带上)
if [ "$FLAG_COUNT" -gt 1 ] 2>/dev/null; then
    CODEX_PROMPT="$CODEX_PROMPT

注意: 这是多 flag 题目，共 $FLAG_COUNT 个 flag，全部拿到才算通关。
progress.md 的 Flags Found 段里可能已有之前获得的 flag (已提交计分)，
不要重复提交它们，也不要重复攻击已拿过 flag 的入口，去寻找剩余的 flag
(通常意味着换攻击点/换入口/深入下一阶段)。每拿到一个新 flag 立即追加到
Flags Found 段 (一行一个)。"
fi

RETRY=0

# 全新目录: 等待 Hermes 预热完成 board.md 初始化（Codex 首轮启动前 board 必须有上下文）
# 等 warmup 完成信号（.hermes_session 成功 / .hermes_warmup_failed 失败，最多 360s）
if [ "$IS_RESUME" = "0" ]; then
    echo "[run.sh] 等待 Hermes 初始化 board.md (最长 360s)..."
    for i in $(seq 1 60); do
        if grep -q "^| M[0-9]" "$WORK_DIR/board.md" 2>/dev/null; then
            echo "[run.sh] board.md 已就绪 ($(grep -c '^| M[0-9]' "$WORK_DIR/board.md") 条 Memory)"
            break
        fi
        if [ -f "$WORK_DIR/.hermes_warmup_failed" ]; then
            echo "[run.sh] WARNING: Hermes 预热失败，board.md 可能未初始化"
            break
        fi
        sleep 6
    done
    if ! grep -q "^| M[0-9]" "$WORK_DIR/board.md" 2>/dev/null; then
        echo "[run.sh] WARNING: board.md 未就绪（Hermes 预热超时），Codex 将自行读 board 并继续"
    fi
fi

while [ $RETRY -lt $MAX_RETRIES ] && [ $INTERRUPTED -eq 0 ]; do
    ROUND=$((RETRY+1))
    echo ""
    echo "=== Codex round $ROUND/$MAX_RETRIES ==="

    cd "$WORK_DIR"
    # codex exec 崩溃恢复: deepseek responses API 偶发 "No tool output found"
    # (第三方模型工具往返 bug)。判定: exit 非 0 且日志尾部 10 行含该错误
    # (一轮内中途的 No tool output 是 codex 内部已恢复, exit 0 正常切换, 不算崩)。
    # 崩溃 → `codex exec resume <sid>` 拉起原会话 (resume 是 exec 子命令,
    # --resume 是非法参数会 exit 2 —— 曾导致 resume 兜底全部失效)。
    # 崩了就拉: 偶发崩溃 resume 后 API 恢复即可继续。但若崩溃是确定性的
    # (同一工具调用必现 No tool output, resume 重放同 session 必重崩 —— 实测 b-01),
    # 无限 resume 会死循环空转烧 token → 连续 RESUME_MAX 次失败即放弃本 session,
    # 收工检查后下一轮换全新 session (从 board/progress 恢复, 不重放崩溃现场)。
    RESUME_ARGS=()
    RESUME_FAILS=0
    RESUME_MAX=3
    while true; do
        EXIT_CODE=0
        codex exec --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust \
          --ignore-rules --disable guardian_approval --skip-git-repo-check \
          ${RESUME_ARGS[@]+"${RESUME_ARGS[@]}"} \
          "$CODEX_PROMPT" \
            < /dev/null > codex.log 2>&1 || EXIT_CODE=$?
        # 注意: 必须 || 捕获退出码 (set -e 下 codex 非 0 退出会直接终止脚本!)
        cat codex.log >> "codex_round${ROUND}.log" 2>/dev/null || true
        if [ $EXIT_CODE -eq 0 ]; then
            break
        fi
        if [ $INTERRUPTED -eq 1 ]; then
            break
        fi
        if tail -10 codex.log | grep -q "No tool output found" 2>/dev/null; then
            # 提取本次 session id，resume 恢复原会话 (完整对话延续)
            SID=$(grep -oP "session id: \K[0-9a-f-]+" codex.log | tail -1)
            if [ -n "$SID" ] && [ "$RESUME_FAILS" -lt "$RESUME_MAX" ]; then
                RESUME_FAILS=$((RESUME_FAILS+1))
                RESUME_ARGS=(resume "$SID")
                echo "[run.sh] codex 崩溃 (No tool output found, exit=$EXIT_CODE)，resume 拉起原会话 ($RESUME_FAILS/$RESUME_MAX) $(date +%H:%M:%S)"
                sleep 5
                continue
            fi
            if [ "$RESUME_FAILS" -ge "$RESUME_MAX" ]; then
                echo "[run.sh] 连续 $RESUME_MAX 次 resume 失败 (确定性崩溃)，放弃本 session，下一轮换全新 session"
            fi
        fi
        # 非工具往返错误 / 拿不到 SID / resume 超阈值 → 收工检查 (下一轮新 session)
        echo "[run.sh] codex exec 退出码 $EXIT_CODE (无法恢复)，进入收工检查"
        break
    done

    # Ctrl+C 被按下 -> 不续跑，直接退出
    if [ $INTERRUPTED -eq 1 ]; then
        break
    fi

    # ── 收工确认制 (每轮先纠错，再判定通关) ──
    # 1. 纠错: 从 submit_results.jsonl 找平台判定 wrong 的 flag → 清 progress.md + 写 dead_ends。
    #    与"是否拿满"解耦: 多 flag 中途的假 flag 也当场纠错，Codex 下轮绕开。
    python3 - "$WORK_DIR" << 'PYEOF' || true
import sys, os, json
work_dir = sys.argv[1]
pp = os.path.join(work_dir, "progress.md")
jr = os.path.join(work_dir, "submit_results.jsonl")
de = os.path.join(work_dir, "dead_ends.md")
if not os.path.exists(jr):
    sys.exit(0)
try:
    records = [json.loads(l) for l in open(jr, encoding="utf-8") if l.strip()]
    bad = list(dict.fromkeys(r["flag"] for r in records if r.get("status") == "wrong"))
except Exception:
    sys.exit(0)
if not bad:
    sys.exit(0)
try:
    lines = open(pp, encoding="utf-8").read().splitlines(keepends=True)
    in_flags = False
    out = []
    for ln in lines:
        if ln.startswith("## Flags Found"):
            in_flags = True
            out.append(ln)
            continue
        if in_flags and ln.startswith("## "):
            in_flags = False
        if in_flags and any(b in ln for b in bad):
            continue  # 移除假 flag 行
        out.append(ln)
    open(pp, "w", encoding="utf-8").writelines(out)
except Exception:
    pass
try:
    existing = open(de, encoding="utf-8").read() if os.path.exists(de) else ""
    with open(de, "a", encoding="utf-8") as f:
        for b in bad:
            if b not in existing:
                f.write(f"\n🚫 平台判定错误: {b} —— 假 flag/诱饵 (已提交被平台拒绝)，禁止再提交；"
                        f"继续挖掘其他攻击面/凭据/文件。\n")
except Exception:
    pass
PYEOF

    # 2. 检测 progress.md 的 Flags Found 段 (Codex 主动声明的，不碰 codex.log)
    #    注意: 过滤 HTML 注释 / 进度笔记 (与 master/challenge_state.py 的 _looks_like_flag 一致)
    FLAGS_ALL=$(awk '/^## *Flags Found/{f=1;next} /^##/{f=0} f' "$WORK_DIR/progress.md" \
        | grep -v '^(无)' | grep -v '^<!--' | grep -v '^$' \
        | grep -v ' ' \
        | python3 -c "import sys; [print(l.rstrip()) for l in sys.stdin if not any('\u4e00' <= c <= '\u9fff' for c in l)]" \
        | awk 'length($0) <= 128 && $0 !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/' || true)
    FLAGS_COUNT=$(echo "$FLAGS_ALL" | grep -c . 2>/dev/null | tr -d ' ' || true)
    FIRST_FLAG=$(echo "$FLAGS_ALL" | head -1)
    if [ -n "$FIRST_FLAG" ]; then
        if [ "$FLAGS_COUNT" -ge "$FLAG_COUNT" ] 2>/dev/null || [ "$FLAG_COUNT" = "1" ]; then
            echo ""
            echo "=== FLAG FOUND! (${FLAGS_COUNT}/${FLAG_COUNT} 全部拿到) ==="
            echo "$FLAGS_ALL"
            # 3. 收工确认: 等所有有效 flag 都有平台判定 (submit_results.jsonl 追加记录)。
            #    全 correct → 收工; 有 wrong → 下轮开头纠错后继续; 超时/无 master → 原行为收工
            echo "=== 等待平台确认 (最长 120s) ==="
            CONFIRMED=""
            for i in $(seq 1 24); do
                RESULT=$(python3 - "$WORK_DIR" << 'PYEOF' || true
import sys, os, json, re
work_dir = sys.argv[1]
jr = os.path.join(work_dir, "submit_results.jsonl")
if not os.path.exists(jr):
    print("PENDING"); sys.exit(0)
try:
    records = [json.loads(l) for l in open(jr, encoding="utf-8") if l.strip()]
    statuses = {r["flag"]: r["status"] for r in records}
except Exception:
    print("PENDING"); sys.exit(0)
# 当前 progress.md 的有效 flag (与 run.sh 过滤同规则)
try:
    text = open(os.path.join(work_dir, "progress.md"), encoding="utf-8").read()
    seg = re.split(r"^## *Flags Found\b", text, flags=re.M)[1]
    seg = re.split(r"^## ", seg, flags=re.M)[0]
    flags = []
    for ln in seg.splitlines():
        ln = ln.strip()
        if not ln or ln == "(无)" or ln.startswith("<!--") or " " in ln:
            continue
        if any("\u4e00" <= c <= "\u9fff" for c in ln) or len(ln) > 128:
            continue
        flags.append(ln)
except Exception:
    flags = []
if not flags:
    print("PENDING"); sys.exit(0)
missing = [f for f in flags if f not in statuses]
if missing:
    print("PENDING"); sys.exit(0)
if all(statuses[f] == "correct" for f in flags):
    print("CORRECT")
else:
    print("WRONG")
PYEOF
)
                if [ "$RESULT" = "CORRECT" ]; then
                    CONFIRMED=correct
                    break
                elif [ "$RESULT" = "WRONG" ]; then
                    CONFIRMED=wrong
                    break
                fi
                sleep 5
            done
            if [ "$CONFIRMED" = "correct" ]; then
                echo "=== 平台确认 FLAG ACCEPTED! ==="
                break
            elif [ "$CONFIRMED" = "wrong" ]; then
                echo "=== 部分 flag 平台判定错误，已记录纠错，继续挖 ==="
                # 不 break: 下一轮开头纠错 python 会清假 flag + 写 dead_ends
            else
                echo "[run.sh] 未收到平台确认 (超时/无 master)，按原行为收工"
                break
            fi
        fi
        echo ""
        echo "=== FLAG FOUND (${FLAGS_COUNT}/${FLAG_COUNT})，多 flag 未拿满，继续攻剩余 ==="
        # 不 break: 下一轮 codex 续跑继续找 (prompt 会带已得 flag 进度)
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
