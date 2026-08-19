#!/usr/bin/env bash
#
# switch-api.sh -- ctf-agent 比赛网关/官方 API 切换脚本 (快照模式)
#
# 用法:
#   ./switch-api.sh gateway   # 生成比赛网关快照 (容器/镜像用网关, 需配合 gateway-proxy.py)
#   ./switch-api.sh official  # 恢复官方 API 快照 (cred_snapshot.py 重新生成)
#   ./switch-api.sh status    # 查看当前快照端点
#
# 背景 (2026-08-19):
#   比赛平台「大模型 API 配置」网关 URL 是原始 URL 的完整代理:
#     anthropic: https://llm-gateway.dasctf.com/llm-gateway/proxy/e/adHBctoNwQbmLUvp
#     responses: https://llm-gateway.dasctf.com/llm-gateway/proxy/e/lFfnjnPhYeLWKnl7
#   POST 网关 URL 本身 = 一次调用 (实测 200), 但 claude/hermes/codex 的 SDK
#   会拼路径 (/v1/messages, /responses) -> 网关URL+路径 -> 404。
#   因此网关快照里三引擎 base_url 全部指向本地代理 http://127.0.0.1:8765
#   (scripts/gateway-proxy.py 剥离 SDK 路径转发到网关完整 URL)。
#
# 重要: 本脚本只改快照 (容器/镜像生效), 绝不碰宿主运行配置
#   (~/.hermes 是 Hermes agent 自己在用, ~/.claude / ~/.codex 是本地开发工具,
#   切坏会导致本地工具全部不可用)。
#
# 流程: gateway: 备份 current 快照 -> 复制官方快照 -> sed 三引擎 base_url
#       -> 更新 current 符号链接。official: cred_snapshot.py 从宿主官方配置重新生成。

set -euo pipefail

GATEWAY_PROXY="http://127.0.0.1:8765"

SNAP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/cred_snapshots"
SNAP_SCRIPT="$SNAP_ROOT/../master/cred_snapshot.py"
BACKUP_DIR="$HOME/.ctf-agent-api-backup"
TS="$(date +%Y%m%d-%H%M%S)"

die() { echo "❌ $*" >&2; exit 1; }

# ── gateway: 生成网关快照 (从 current 官方快照复制 + 改端点) ──
make_gateway_snapshot() {
    [ -d "$SNAP_ROOT/current" ] || die "当前快照不存在: $SNAP_ROOT/current"
    local dst="$SNAP_ROOT/run-gateway-$TS"
    cp -r "$SNAP_ROOT/current" "$dst"
    chmod -R u+w "$dst" 2>/dev/null || true

    # codex: base_url -> 本地代理
    sed -i "s|^base_url = .*|base_url = \"$GATEWAY_PROXY\"|" "$dst/codex/config.toml"
    # hermes: base_url -> 本地代理 + api_mode=anthropic_messages
    sed -i "s|^  base_url: .*|  base_url: $GATEWAY_PROXY|" "$dst/hermes/config.yaml"
    sed -i "s|^  api_mode: .*|  api_mode: anthropic_messages|" "$dst/hermes/config.yaml"
    # claude: ANTHROPIC_BASE_URL -> 本地代理
    python3 - "$dst/claude/settings.json" "$GATEWAY_PROXY" << 'PY'
import json, os, sys
p, url = sys.argv[1], sys.argv[2]
cfg = json.load(open(p, encoding="utf-8"))
env = cfg.setdefault("env", {})
env["ANTHROPIC_BASE_URL"] = url
json.dump(cfg, open(p, "w"), indent=2, ensure_ascii=False)
PY

    # 更新 current 符号链接
    ln -sfn "$(basename "$dst")" "$SNAP_ROOT/current"
    echo "✅ 已生成比赛网关快照: $(basename "$dst")"
    echo "   三引擎 base_url = $GATEWAY_PROXY (本地代理)"
    echo "   容器/镜像下次启动生效 (配合 scripts/gateway-proxy.py)"
}

# ── official: 恢复官方快照 (宿主配置不动) ──
make_official_snapshot() {
    [ -f "$SNAP_SCRIPT" ] || die "未找到 $SNAP_SCRIPT"
    python3 "$SNAP_SCRIPT" 2>&1 | tail -1
    echo "✅ 已恢复官方快照 (current -> $(readlink "$SNAP_ROOT/current"))"
    echo "   容器/镜像下次启动生效"
}

show_status() {
    echo "=== 当前快照端点 (容器生效) ==="
    echo "--- codex ---"
    grep -E "base_url|wire_api" "$SNAP_ROOT/current/codex/config.toml" 2>/dev/null | head -2
    echo "--- hermes ---"
    grep -E "base_url|api_mode" "$SNAP_ROOT/current/hermes/config.yaml" 2>/dev/null | head -2
    echo "--- claude ---"
    grep -oE "https?://[^\"]*" "$SNAP_ROOT/current/claude/settings.json" 2>/dev/null | head -2
    echo "--- 宿主 (不受影响) ---"
    grep -E "base_url" "$HOME/.codex/config.toml" 2>/dev/null | head -1
    grep -E "base_url|api_mode" "$HOME/.hermes/config.yaml" 2>/dev/null | head -2
}

case "${1:-}" in
    gateway)
        mkdir -p "$BACKUP_DIR"
        [ -L "$SNAP_ROOT/current" ] && cp -rL "$SNAP_ROOT/current" "$BACKUP_DIR/snapshot-official.$TS" 2>/dev/null || true
        make_gateway_snapshot
        ;;
    official)
        make_official_snapshot
        ;;
    status)
        show_status
        ;;
    *)
        echo "用法: $0 {gateway|official|status}"
        echo "  gateway  → 生成比赛网关快照 (容器用网关, 配合 gateway-proxy.py)"
        echo "  official → 恢复官方快照"
        echo "  status   → 查看当前快照端点"
        exit 1
        ;;
esac
