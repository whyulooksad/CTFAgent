#!/usr/bin/env bash
#
# switch-api.sh -- ctf-agent API 端点切换脚本（官方 API ↔ 比赛网关）
#
# 用法:
#   ./switch-api.sh gateway   # 切到比赛网关 (llm-gateway.dasctf.com)
#   ./switch-api.sh official  # 切回官方 API (api.deepseek.com)
#   ./switch-api.sh status    # 查看当前端点
#
# 背景 (2026-08-18):
#   比赛网关只代理两条路径, 没有 /v1/chat/completions:
#     /responses            -> https://llm-gateway.dasctf.com/llm-gateway/proxy/e/lFfnjnPhYeLWKnl7
#     /anthropic/v1/messages -> https://llm-gateway.dasctf.com/llm-gateway/proxy/e/adHBctoNwQbmLUvp
#   因此:
#     codex (wire_api=responses) : 只换 base_url 为网关 /responses URL
#     hermes (api_mode=chat_completions → anthropic_messages) : 换 base_url + 换 api_mode
#   模型名不变 (deepseek-v4-pro / deepseek-v4-flash)。
#
# 流程: 备份原配置 -> 修改宿主 ~/.codex + ~/.hermes -> 重新生成快照 (容器下次启动生效)

set -euo pipefail

GATEWAY_RESPONSES="https://llm-gateway.dasctf.com/llm-gateway/proxy/e/lFfnjnPhYeLWKnl7"
GATEWAY_ANTHROPIC="https://llm-gateway.dasctf.com/llm-gateway/proxy/e/adHBctoNwQbmLUvp"
OFFICIAL_RESPONSES="https://api.deepseek.com"
OFFICIAL_ANTHROPIC="https://api.deepseek.com/v1"

CODEX_CFG="$HOME/.codex/config.toml"
HERMES_CFG="$HOME/.hermes/config.yaml"
SNAP_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/master/cred_snapshot.py"
BACKUP_DIR="$HOME/.ctf-agent-api-backup"
TS="$(date +%Y%m%d-%H%M%S)"

die() { echo "❌ $*" >&2; exit 1; }

backup() {
    mkdir -p "$BACKUP_DIR"
    [ -f "$CODEX_CFG" ]  && cp "$CODEX_CFG"  "$BACKUP_DIR/codex-config.toml.$TS"
    [ -f "$HERMES_CFG" ] && cp "$HERMES_CFG" "$BACKUP_DIR/hermes-config.yaml.$TS"
    echo "[backup] 配置已备份到 $BACKUP_DIR/ (时间戳 $TS)"
}

# 替换 codex config.toml 的 base_url 行
set_codex_base() {
    local url="$1"
    sed -i "s|^base_url = .*|base_url = \"$url\"|" "$CODEX_CFG"
}

# 替换 hermes config.yaml 的 base_url / api_mode 行
set_hermes() {
    local url="$1" mode="$2"
    sed -i "s|^  base_url: .*|  base_url: $url|" "$HERMES_CFG"
    sed -i "s|^  api_mode: .*|  api_mode: $mode|" "$HERMES_CFG"
}

regenerate_snapshot() {
    if [ -f "$SNAP_SCRIPT" ]; then
        echo "[snapshot] 重新生成快照..."
        python3 "$SNAP_SCRIPT" 2>&1 | tail -1
    else
        echo "[snapshot] ⚠️ 未找到 $SNAP_SCRIPT，跳过（容器不会更新，手动运行）"
    fi
}

show_status() {
    echo "=== 当前端点 ==="
    echo "--- codex ($CODEX_CFG) ---"
    grep -E "base_url|wire_api|^model" "$CODEX_CFG" 2>/dev/null | head -4
    echo "--- hermes ($HERMES_CFG) ---"
    grep -E "base_url|api_mode|^model|default" "$HERMES_CFG" 2>/dev/null | head -5
    echo "--- 快照 (容器生效) ---"
    grep -E "base_url|wire_api" /home/stw/ctf-agent/cred_snapshots/current/codex/config.toml 2>/dev/null | head -2
    grep -E "base_url|api_mode" /home/stw/ctf-agent/cred_snapshots/current/hermes/config.yaml 2>/dev/null | head -2
}

case "${1:-}" in
    gateway)
        backup
        set_codex_base "$GATEWAY_RESPONSES"
        set_hermes "$GATEWAY_ANTHROPIC" "anthropic_messages"
        echo "✅ 已切换到比赛网关:"
        echo "   codex  base_url = $GATEWAY_RESPONSES (responses)"
        echo "   hermes base_url = $GATEWAY_ANTHROPIC (anthropic_messages)"
        regenerate_snapshot
        ;;
    official)
        backup
        set_codex_base "$OFFICIAL_RESPONSES"
        set_hermes "$OFFICIAL_ANTHROPIC" "chat_completions"
        echo "✅ 已切回官方 API:"
        echo "   codex  base_url = $OFFICIAL_RESPONSES (responses)"
        echo "   hermes base_url = $OFFICIAL_ANTHROPIC (chat_completions)"
        regenerate_snapshot
        ;;
    status)
        show_status
        ;;
    *)
        echo "用法: $0 {gateway|official|status}"
        echo "  gateway  → 切到比赛网关"
        echo "  official → 切回官方 API"
        echo "  status   → 查看当前端点"
        exit 1
        ;;
esac
