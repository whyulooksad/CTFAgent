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

# ── 0. HOME 修正: 镜像以 root 跑 (HOME=/root), 但全部配置在 /home/ubuntu/ 下 ──
# 不设则 claude/codex/hermes 读 $HOME/.xxx 全部落空 → 用默认配置连公网 → 沙箱无公网 → 卡死
# (两次托管 claude exit 124 的根因; 平台审计证实 claude 请求从未发出)
export HOME=/home/ubuntu
echo "[hosted] HOME=${HOME} (配置读取修正)"

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
# 托管超时: 引擎调用 15 分钟上限 (防网关挂起时 run.sh 无限等; 超时→收工检查→下一轮)
export HOSTED_TIMEOUT=900
# 沙箱无公网: 禁用 claude 启动时的版本检查/遥测等非必要流量
# (实测: 不设则 claude 启动挂起连公网 → 90s 超时被杀, curl 网关却全通)
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export DISABLE_TELEMETRY=1
export CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1

echo "[hosted] BENCHMARK_BASE_URL=${BENCHMARK_BASE_URL:-<未注入>}"

# ── 5. 模型链路自检 (平台日志 30 秒内见分晓, 不再静默卡 15 分钟) ──
# 网关域名两种变体都测: http (平台规则推荐) 和 https (若网关支持则更稳)
# 三个协议路径分别测: OpenAI chat (hermes/codex-chat) / responses (codex) / anthropic (claude)
echo "[hosted] ==== 自检 1/2: 模型网关连通性 ===="
GW_HOST="api.deepseek.com.tsecbench.gw"
GW_BODY='{"model":"deepseek-chat","messages":[{"role":"user","content":"ping"}],"max_tokens":5}'
GW_HTTP=$(curl -sS -m 20 -o /tmp/gw_chat.json -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${DEEPSEEK_API_KEY}" \
    -d "$GW_BODY" "http://${GW_HOST}/v1/chat/completions" 2>&1 || echo "CURL_FAIL")
GW_HTTPS=$(curl -sS -m 20 -o /dev/null -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${DEEPSEEK_API_KEY}" \
    -d "$GW_BODY" "https://${GW_HOST}/v1/chat/completions" 2>&1 || echo "CURL_FAIL")
echo "[hosted] 网关 /v1/chat/completions (OpenAI标准): http=${GW_HTTP} https=${GW_HTTPS}"
echo "[hosted] 响应: $(head -c 300 /tmp/gw_chat.json 2>/dev/null)"

# anthropic 协议路径 (claude 用): 网关若不转发则 claude 必卡
GW_ANTH=$(curl -sS -m 20 -o /tmp/gw_anth.json -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -H "x-api-key: ${DEEPSEEK_API_KEY}" \
    -H "anthropic-version: 2023-06-01" \
    -d '{"model":"deepseek-v4-pro","max_tokens":5,"messages":[{"role":"user","content":"ping"}]}' \
    "http://${GW_HOST}/anthropic/v1/messages" 2>&1 || echo "CURL_FAIL")
echo "[hosted] 网关 /anthropic/v1/messages (claude协议): ${GW_ANTH}"
echo "[hosted] 响应: $(head -c 300 /tmp/gw_anth.json 2>/dev/null)"

# responses 协议路径 (codex 用)
GW_RESP=$(curl -sS -m 20 -o /tmp/gw_resp.json -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${DEEPSEEK_API_KEY}" \
    -d '{"model":"deepseek-v4-pro","input":"ping","max_output_tokens":5}' \
    "http://${GW_HOST}/v1/responses" 2>&1 || echo "CURL_FAIL")
echo "[hosted] 网关 /v1/responses (codex协议): ${GW_RESP}"
echo "[hosted] 响应: $(head -c 300 /tmp/gw_resp.json 2>/dev/null)"

# 流式探测 (claude/codex 都强制 SSE 流式; 非流式 200 但流式挂起 = 网关流式问题)
GW_STREAM=$(curl -sS -N -m 25 -o /tmp/gw_stream.txt -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${DEEPSEEK_API_KEY}" \
    -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"ping"}],"max_tokens":5,"stream":true}' \
    "http://${GW_HOST}/v1/chat/completions" 2>&1 || echo "CURL_FAIL")
echo "[hosted] 网关流式(SSE) chat: ${GW_STREAM} 首字节: $(head -c 150 /tmp/gw_stream.txt 2>/dev/null | tr '\n' ' ')"

# anthropic 流式 (claude 用): 决定 claude 是否可用
GW_ANTH_STREAM=$(curl -sS -N -m 25 -o /tmp/gw_anth_stream.txt -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -H "x-api-key: ${DEEPSEEK_API_KEY}" \
    -H "anthropic-version: 2023-06-01" \
    -d '{"model":"deepseek-v4-pro","max_tokens":5,"stream":true,"messages":[{"role":"user","content":"ping"}]}' \
    "http://${GW_HOST}/anthropic/v1/messages" 2>&1 || echo "CURL_FAIL")
echo "[hosted] 网关流式(SSE) anthropic: ${GW_ANTH_STREAM} 首字节: $(head -c 150 /tmp/gw_anth_stream.txt 2>/dev/null | tr '\n' ' ')"

# responses 流式 (codex 用): 决定 codex 是否可用
GW_RESP_STREAM=$(curl -sS -N -m 25 -o /tmp/gw_resp_stream.txt -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${DEEPSEEK_API_KEY}" \
    -d '{"model":"deepseek-v4-pro","input":"ping","max_output_tokens":5,"stream":true}' \
    "http://${GW_HOST}/v1/responses" 2>&1 || echo "CURL_FAIL")
echo "[hosted] 网关流式(SSE) responses: ${GW_RESP_STREAM} 首字节: $(head -c 150 /tmp/gw_resp_stream.txt 2>/dev/null | tr '\n' ' ')"

GW_SCHEME="http"
if [ "$GW_HTTP" = "200" ]; then
    echo "[hosted] 自检 1/2 通过: http 网关正常"
elif [ "$GW_HTTPS" = "200" ]; then
    echo "[hosted] http 网关异常但 https 正常 → 三个配置切回 https"
    GW_SCHEME="https"
    sed -i "s|http://${GW_HOST}|https://${GW_HOST}|g" \
        /home/ubuntu/.codex/config.toml \
        /home/ubuntu/.hermes/config.yaml \
        /home/ubuntu/.hermes/.env \
        /home/ubuntu/.claude/settings.json 2>/dev/null || true
    echo "[hosted] 已切换 https 网关"
elif [ "$GW_HTTP" = "302" ]; then
    echo "[hosted] !! 网关 http 返回 302 (重定向到 https): 模型域名需走 https" >&2
else
    echo "[hosted] !! 网关异常 (http=${GW_HTTP}, https=${GW_HTTPS}): 模型链路不通" >&2
fi
export GW_SCHEME

# ── 引擎选择: 主解题 Agent = claude (cc), hermes 仅最后兜底 ──
# 优先级: claude > codex > hermes (架构要求 cc 主解题; hermes 只是监督者)
# 注: 前两次托管 claude 卡死根因 = HOME=/root 读不到配置 (已修正 HOME)
AGENT_CLI_FINAL="claude"
echo "[hosted] ==== 自检 2/2: 引擎最小调用 (${AGENT_CLI_FINAL}) ===="
timeout 90 claude -p "ping" --output-format text 2>&1 | tail -4
CC_EXIT=${PIPESTATUS[0]}
echo "[hosted] claude 自检 exit: ${CC_EXIT}"
if [ "$CC_EXIT" != "0" ]; then
    echo "[hosted] !! claude 不可用 (exit ${CC_EXIT}) → 尝试 codex"
    timeout 90 codex exec --skip-git-repo-check "ping" 2>&1 | tail -3
    CX_EXIT=${PIPESTATUS[0]}
    echo "[hosted] codex 自检 exit: ${CX_EXIT}"
    if [ "$CX_EXIT" = "0" ]; then
        AGENT_CLI_FINAL="codex"
        sed -i 's|"agent_cli": "claude"|"agent_cli": "codex"|' \
            /opt/ctf-agent/master/master_config.hosted.json
        echo "[hosted] master_config 已切换 agent_cli=codex"
    else
        echo "[hosted] !! codex 也不可用 → 最后兜底 hermes"
        AGENT_CLI_FINAL="hermes"
        sed -i 's|"agent_cli": "claude"|"agent_cli": "hermes"|' \
            /opt/ctf-agent/master/master_config.hosted.json
        echo "[hosted] master_config 已切换 agent_cli=hermes (兜底)"
    fi
fi
export AGENT_CLI_FINAL
echo "[hosted] 最终引擎: ${AGENT_CLI_FINAL}"

echo "[hosted] 启动 master (adapter=tsec backend=process)..."
# 降权 ubuntu: claude --dangerously-skip-permissions 拒绝 root (实测 exit 1 秒失败)
# 本地 DockerBackend 用 --user 1000:1000 跑 ubuntu 所以正常; 托管 ProcessBackend 继承 root 必须降权
chown -R ubuntu:ubuntu /opt/ctf-agent /home/ubuntu 2>/dev/null || true
echo "[hosted] 已降权 ubuntu 用户 (chown /opt/ctf-agent /home/ubuntu)"

exec su ubuntu -s /bin/bash -c 'cd /opt/ctf-agent && exec python3 master/master.py --config master/master_config.hosted.json'
