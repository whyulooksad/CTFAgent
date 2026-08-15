# ctf-solver -- 单题 Solver 容器镜像 (master-agent-spec.md §4.4 / §7)
#
# 构建: docker/solver/build.sh (会先把宿主机 hermes 源码同步到 hermes-src/)
# 内容:
#   - Python 3.11 + CTF 工具链 (nmap/sqlmap/ffuf/gobuster/dirsearch/wfuzz/binwalk/steghide/exiftool...)
#   - Codex CLI 锁定 0.147.0 (--profile ctf 的 ctf.config.toml 覆盖机制依赖该版本行为)
#   - Hermes Agent 源码原生安装 (Linux venv，宿主机 venv 是 macOS 二进制不能拷贝)
#   - 本项目 COPY 进 /opt/ctf-agent 并 git init (hook 的 git rev-parse 双保险；
#     实际 hook 命令已在精制快照中重写为绝对路径)
# 运行时由 DockerBackend 挂载:
#   -v challenges:/opt/ctf-agent/challenges   # 题目现场 (宿主机可见，删容器不删数据)
#   -v <snapshot>/codex:/root/.codex          # 精制快照 (cred_snapshot.py 生成)
#   -v <snapshot>/hermes:/root/.hermes
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
           nodejs npm; then ok=1; break; fi; \
      echo "apt 安装失败，重试 $i/3"; sleep 5; apt-get update; \
    done; \
    [ "$ok" = "1" ]; \
    rm -rf /var/lib/apt/lists/*

# ── Codex CLI (版本锁定，与宿主机一致) ──
ARG CODEX_VERSION=0.147.0
RUN set -eux; \
    if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi; \
    npm install -g --omit=dev @openai/codex@${CODEX_VERSION}

# ── 二进制题工具链: pwntools (exploit 编写) ──
# pip 带重试: 镜像源对个别包偶发 403 (实测 TUNA openai wheel 瞬时限流)
RUN set -eux; \
    if [ -n "$PIP_INDEX_URL" ]; then \
      pip install --no-cache-dir --retries 5 -i "$PIP_INDEX_URL" pwntools; \
    else \
      pip install --no-cache-dir --retries 5 pwntools; \
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
