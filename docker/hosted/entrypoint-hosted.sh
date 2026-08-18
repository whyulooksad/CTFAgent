#!/bin/bash
# 托管模式入口 (平台沙箱运行)
#
# 平台托管要求:
#   - 沙箱无公网, 大模型 API 必须走平台网关
#   - 网关规则: 域名加 .tsecbench.gw 后缀 + https→http
#   - deepseek 在白名单: https://api.deepseek.com/* → http://api.deepseek.com.tsecbench.gw/*
#   - 平台注入环境变量: BENCHMARK_TOKEN / BENCHMARK_BASE_URL (答题 API)
#   - 沙箱无 docker → master 用 process backend (直接 bash run.sh 起 solver 子进程)
set -e

echo "[hosted] 托管模式启动, 切换大模型地址到平台网关..."

GW_DOMAIN="http://api.deepseek.com.tsecbench.gw"

# ── 1. codex (responses 协议) ──
if [ -f /home/ubuntu/.codex/config.toml ]; then
    sed -i "s|https://api.deepseek.com|${GW_DOMAIN}|" /home/ubuntu/.codex/config.toml
    echo "[hosted] codex  → ${GW_DOMAIN}"
fi

# ── 2. hermes (chat_completions) ──
if [ -f /home/ubuntu/.hermes/config.yaml ]; then
    sed -i "s|https://api.deepseek.com|${GW_DOMAIN}|" /home/ubuntu/.hermes/config.yaml
    echo "[hosted] hermes → ${GW_DOMAIN}"
fi

# ── 3. claude (anthropic 协议) ──
if [ -f /home/ubuntu/.claude/settings.json ]; then
    sed -i "s|https://api.deepseek.com|${GW_DOMAIN}|" /home/ubuntu/.claude/settings.json
    echo "[hosted] claude → ${GW_DOMAIN}"
fi

echo "[hosted] BENCHMARK_BASE_URL=${BENCHMARK_BASE_URL:-<未注入>}"
echo "[hosted] 启动 master (adapter=tsec backend=process)..."

exec python3 /opt/ctf-agent/master/master.py \
    --config /opt/ctf-agent/master/master_config.hosted.json
