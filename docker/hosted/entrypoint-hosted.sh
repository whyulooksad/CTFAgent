#!/bin/bash
# 托管模式入口 (平台沙箱运行)
#
# 平台托管要求:
#   - 沙箱无公网, 大模型 API 必须走平台网关
#   - 网关规则: 域名加 .tsecbench.gw 后缀 + https→http
#   - deepseek 在白名单: https://api.deepseek.com/* → http://api.deepseek.com.tsecbench.gw/*
#   - 平台注入环境变量: BENCHMARK_TOKEN / BENCHMARK_BASE_URL (答题 API)
#   - 运行时环境变量 (平台页面配置): DEEPSEEK_API_KEY (大模型密钥)
#   - 镜像内不含任何密钥: codex/hermes/claude 配置均为 {{DEEPSEEK_API_KEY}} 占位符
#   - 沙箱无 docker → master 用 process backend (直接 bash run.sh 起 solver 子进程)
set -e

echo "[hosted] 托管模式启动..."

# ── 0. 密钥注入: 从环境变量替换占位符 (镜像内无任何真实 key) ──
if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
    echo "[hosted] !! 错误: 环境变量 DEEPSEEK_API_KEY 未配置 (平台页面「运行时环境变量」需填写)" >&2
    exit 1
fi

GW_DOMAIN="http://api.deepseek.com.tsecbench.gw"

# ── 1. codex (responses 协议) ──
if [ -f /home/ubuntu/.codex/config.toml ]; then
    sed -i "s|https://api.deepseek.com|${GW_DOMAIN}|" /home/ubuntu/.codex/config.toml
    sed -i "s|{{DEEPSEEK_API_KEY}}|${DEEPSEEK_API_KEY}|" /home/ubuntu/.codex/config.toml
    echo "[hosted] codex  → ${GW_DOMAIN} (key 已注入)"
fi

# ── 2. hermes (chat_completions, 读 .env 的 DEEPSEEK_API_KEY) ──
if [ -f /home/ubuntu/.hermes/config.yaml ]; then
    sed -i "s|https://api.deepseek.com|${GW_DOMAIN}|" /home/ubuntu/.hermes/config.yaml
    echo "[hosted] hermes → ${GW_DOMAIN}"
fi
if [ -f /home/ubuntu/.hermes/.env ]; then
    sed -i "s|{{DEEPSEEK_API_KEY}}|${DEEPSEEK_API_KEY}|" /home/ubuntu/.hermes/.env
fi

# ── 3. claude (anthropic 协议) ──
if [ -f /home/ubuntu/.claude/settings.json ]; then
    sed -i "s|https://api.deepseek.com|${GW_DOMAIN}|" /home/ubuntu/.claude/settings.json
    sed -i "s|{{DEEPSEEK_API_KEY}}|${DEEPSEEK_API_KEY}|" /home/ubuntu/.claude/settings.json
    echo "[hosted] claude → ${GW_DOMAIN} (key 已注入)"
fi

# 4. 同步到进程环境 (run.sh 内 claude/codex 子进程直接继承)
export DEEPSEEK_API_KEY
export ANTHROPIC_AUTH_TOKEN="$DEEPSEEK_API_KEY"

echo "[hosted] BENCHMARK_BASE_URL=${BENCHMARK_BASE_URL:-<未注入>}"
echo "[hosted] 启动 master (adapter=tsec backend=process)..."

exec python3 /opt/ctf-agent/master/master.py \
    --config /opt/ctf-agent/master/master_config.hosted.json
