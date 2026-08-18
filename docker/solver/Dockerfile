# ctf-solver -- 单题 Solver 容器镜像 (master-agent-spec.md §4.4 / §7)
#
# 构建: docker/solver/build.sh (会先把宿主机 hermes 源码同步到 hermes-src/)
# 内容:
#   - Python 3.11 + CTF 工具链 (nmap/sqlmap/ffuf/gobuster/dirsearch/wfuzz/binwalk/steghide/exiftool...)
#   - Claude Code CLI 锁定版本 (解题引擎；通过环境变量接入赛方大模型平台，无凭据快照)
#   - Hermes Agent 源码原生安装 (Linux venv，宿主机 venv 是 macOS 二进制不能拷贝)
#   - 本项目 COPY 进 /opt/ctf-agent 并 git init
# 运行时由 DockerBackend 挂载:
#   -v challenges:/opt/ctf-agent/challenges   # 题目现场 (宿主机可见，删容器不删数据)
#   -v <snapshot>/hermes:/root/.hermes        # hermes 精制快照 (cred_snapshot.py 生成)
#   -v llm.yaml:/opt/ctf-agent/llm.yaml:ro    # 赛方大模型平台接入 (存在才挂，含 api_key 不进镜像)
#
# 注意: 镜像架构跟随构建机 (Apple Silicon 构建 = linux/arm64，WSL x86 机器需
# 在 WSL 上用 build.sh 重新构建 = linux/amd64)。

FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# 可选镜像源 (build.sh 默认传国内源；海外构建可置空用官方源)
ARG APT_MIRROR=""
ARG NPM_REGISTRY=""
ARG PIP_INDEX_URL=""

# ── 系统工具 + CTF 工具链 (Debian 仓库全部有包，已探针验证) ──
# apt 带重试: 镜像源偶发 502
RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org/debian|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    apt-get update; \
    ok=0; \
    for i in 1 2 3; do \
      if apt-get install -y --no-install-recommends \
           bash git curl wget unzip zip file jq xxd \
           nmap sqlmap netcat-openbsd \
           ffuf gobuster dirsearch wfuzz \
           binwalk steghide libimage-exiftool-perl poppler-utils \
           binutils gdb ltrace strace socat \
           dirb tshark foremost \
           nodejs npm; then ok=1; break; fi; \
      echo "apt 安装失败，重试 $i/3"; sleep 5; apt-get update; \
    done; \
    [ "$ok" = "1" ]; \
    rm -rf /var/lib/apt/lists/*

# ── Claude Code CLI (解题引擎，版本锁定与宿主机一致) ──
# claude code 用环境变量接入赛方大模型平台 (ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL，
# 由挂载的 llm.yaml 在 run.sh 里导出)，镜像本身不带任何凭据
ARG CLAUDE_VERSION=2.1.220
RUN set -eux; \
    if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi; \
    npm install -g --omit=dev @anthropic-ai/claude-code@${CLAUDE_VERSION}

# root 容器内允许 --dangerously-skip-permissions (claude code 的 root 保护绕行)，
# 并关掉非必要外联 (遥测等)
ENV IS_SANDBOX=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

# ── 解题 Python 库: pwntools(二进制) + z3/pycryptodome/Pillow/requests (TOOLS.md 承诺的环境) ──
# pip 带重试: 镜像源对个别包偶发 403 (实测 TUNA openai wheel 瞬时限流)
RUN set -eux; \
    if [ -n "$PIP_INDEX_URL" ]; then \
      pip install --no-cache-dir --retries 5 -i "$PIP_INDEX_URL" pwntools z3-solver pycryptodome Pillow requests; \
    else \
      pip install --no-cache-dir --retries 5 pwntools z3-solver pycryptodome Pillow requests; \
    fi

# ── Hermes Agent (build.sh 从宿主机同步源码，排除 venv/node_modules 等) ──
# 注意: hermes 官方禁止 wheel/sdist 构建，必须 editable 安装 (pip install -e)
COPY docker/solver/hermes-src /opt/hermes/hermes-agent
RUN set -eux; \
    python -m venv /opt/hermes/venv; \
    if [ -n "$PIP_INDEX_URL" ]; then \
      /opt/hermes/venv/bin/pip install --no-cache-dir --retries 5 -i "$PIP_INDEX_URL" -e /opt/hermes/hermes-agent; \
    else \
      /opt/hermes/venv/bin/pip install --no-cache-dir --retries 5 -e /opt/hermes/hermes-agent; \
    fi; \
    printf '#!/usr/bin/env bash\nexec /opt/hermes/venv/bin/python /opt/hermes/hermes-agent/hermes "$@"\n' \
      > /usr/local/bin/hermes; \
    chmod +x /usr/local/bin/hermes

# ── 本项目 ──
COPY . /opt/ctf-agent
RUN git -C /opt/ctf-agent init -q

WORKDIR /opt/ctf-agent
ENTRYPOINT ["bash", "/opt/ctf-agent/docker/solver/entrypoint.sh"]
