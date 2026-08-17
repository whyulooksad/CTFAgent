# ctf-solver -- 单题 Solver 容器镜像 (main 版，结构对齐 master)
#
# 构建: docker/solver/build.sh (会先把宿主机 hermes 源码同步到 hermes-src/)
# 内容:
#   - Ubuntu 24.04 (noble) + 系统 python3.12 —— 与宿主机一致；radare2 仅在 Ubuntu 仓库有
#     (Debian bookworm/trixie 均已移除 radare2，不能用 python:3.12-slim)
#   - CTF 工具链 (与宿主机 apt 清单对齐: Web/Misc/Crypto/Binary 四类)
#   - Codex CLI 锁定 0.147.0 (与 master-agent 一致)
#   - Hermes Agent 源码原生安装 (venv；宿主机 venv 不能跨平台拷贝)
#   - 本项目 COPY 进 /opt/ctf-agent 并 git init (hook 的 git rev-parse 双保险)
# 运行时挂载:
#   -v challenges:/opt/ctf-agent/challenges   # 题目现场
#   -v <snapshot>/codex:/root/.codex          # 精制快照 (auth/hooks/配置)
#   -v <snapshot>/hermes:/root/.hermes        # 精制快照 (auth/config/skills)
#
# 注意: Ubuntu 最小镜像无 ca-certificates，apt 源先用 http 装证书再换 https；
#       security 源清华无 Release 文件，删除 (构建镜像不需要安全更新)。
#       镜像架构跟随构建机 (WSL amd64 构建 = linux/amd64)

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# 可选镜像源 (build.sh 默认传清华源；海外构建置空用官方源)
ARG APT_MIRROR=""
ARG NPM_REGISTRY=""
ARG PIP_INDEX_URL=""

# ── 系统工具 + CTF 工具链 (与宿主机 apt 清单对齐；不装 sqlmap/gobuster/dirsearch/wfuzz/dirb) ──
# 步骤: http 源(免证书) -> 装 ca-certificates -> 换 https 源 -> 装工具
# apt 带重试: 镜像源偶发 502
RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
      sed -i 's|http://archive.ubuntu.com/ubuntu|'"$APT_MIRROR"'|g' /etc/apt/sources.list.d/ubuntu.sources; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates; \
    ok=0; \
    for i in 1 2 3; do \
      if apt-get install -y --no-install-recommends \
           bash git curl wget unzip zip file jq xxd \
           python3 python3-pip python3-venv \
           nmap netcat-openbsd \
           ffuf \
           binwalk steghide libimage-exiftool-perl \
           binutils gdb ltrace strace socat radare2 make cmake \
           tshark foremost \
           nodejs npm; then ok=1; break; fi; \
      echo "apt 安装失败，重试 $i/3"; sleep 5; apt-get update || true; \
    done; \
    [ "$ok" = "1" ]; \
    rm -rf /var/lib/apt/lists/*

# ── Codex CLI (版本锁定，与宿主机一致) ──
ARG CODEX_VERSION=0.146.0
RUN set -eux; \
    if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi; \
    npm install -g --omit=dev @openai/codex@${CODEX_VERSION}

# ── 解题 Python 库: pwntools/z3/pycryptodome/Pillow/requests + angr (TOOLS.md 承诺的环境) ──
# Ubuntu 系统 python 是 externally-managed (PEP 668)，必须 --break-system-packages
# pip 带重试: 镜像源对个别包偶发 403
RUN set -eux; \
    if [ -n "$PIP_INDEX_URL" ]; then \
      pip3 install --no-cache-dir --break-system-packages --retries 5 -i "$PIP_INDEX_URL" pwntools z3-solver pycryptodome Pillow requests angr; \
    else \
      pip3 install --no-cache-dir --break-system-packages --retries 5 pwntools z3-solver pycryptodome Pillow requests angr; \
    fi

# ── Hermes Agent (build.sh 从宿主机同步源码，排除 venv/node_modules 等) ──
# 注意: hermes 官方禁止 wheel/sdist 构建，必须 editable 安装 (pip install -e)
COPY docker/solver/hermes-src /opt/hermes/hermes-agent
RUN set -eux; \
    python3 -m venv /opt/hermes/venv; \
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

# 非 root 运行支持: DockerBackend 用 --user 1000:1000 (宿主 stw uid) 跑容器，
# 使 work_dir 文件 owner=1000 (宿主可删)；/root 需可进入以读挂载快照
RUN chmod 755 /root

WORKDIR /opt/ctf-agent
ENTRYPOINT ["bash", "/opt/ctf-agent/docker/solver/entrypoint.sh"]
