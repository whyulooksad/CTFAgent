#!/bin/bash
# build.sh -- 构建 ctf-solver 镜像。
#
#   1. 从宿主机 ~/.hermes/hermes-agent 同步源码到 hermes-src/
#      (排除 venv/node_modules 等开发产物，容器内用 Linux venv 原生安装)
#   2. 以仓库根为构建上下文 docker build
#
# 用法:
#   bash docker/solver/build.sh [--no-sync]
# 环境变量:
#   HERMES_SRC     hermes 源码位置 (默认 ~/.hermes/hermes-agent)
#   IMAGE_TAG      镜像标签 (默认 ctf-solver:latest)
#   APT_MIRROR     Debian 源 (默认清华源；海外构建置空用官方源)
#   NPM_REGISTRY   npm 源 (默认 npmmirror；置空用官方源)
#   PIP_INDEX_URL  pip 源 (默认清华源；置空用官方源)

set -euo pipefail

: "${APT_MIRROR:=https://mirrors.tuna.tsinghua.edu.cn/debian}"
: "${NPM_REGISTRY:=https://registry.npmmirror.com}"
: "${PIP_INDEX_URL:=https://pypi.tuna.tsinghua.edu.cn/simple}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HERMES_SRC="${HERMES_SRC:-$HOME/.hermes/hermes-agent}"
IMAGE_TAG="${IMAGE_TAG:-ctf-solver:latest}"
SYNC_DIR="$SCRIPT_DIR/hermes-src"

if [ "${1:-}" != "--no-sync" ]; then
    if [ ! -d "$HERMES_SRC" ]; then
        echo "[build] hermes 源码不存在: $HERMES_SRC" >&2
        exit 1
    fi
    echo "[build] 同步 hermes 源码 $HERMES_SRC -> $SYNC_DIR"
    mkdir -p "$SYNC_DIR"
    rsync -a --delete \
        --exclude venv \
        --exclude node_modules \
        --exclude tests \
        --exclude website \
        --exclude .git \
        --exclude __pycache__ \
        --exclude '*.pyc' \
        --exclude '*.egg-info' \
        "$HERMES_SRC"/ "$SYNC_DIR"/
    # rsync 保留源时间戳，若源目录时间戳异常(如未来时间)会导致容器内 editable 安装
    # 报 "Cannot update time stamp" -- 统一重置为当前时间
    find "$SYNC_DIR" -exec touch {} + 2>/dev/null || true
fi

echo "[build] docker build -t $IMAGE_TAG (context: $REPO_ROOT)"
docker build -f "$SCRIPT_DIR/Dockerfile" -t "$IMAGE_TAG" \
    --build-arg APT_MIRROR="$APT_MIRROR" \
    --build-arg NPM_REGISTRY="$NPM_REGISTRY" \
    --build-arg PIP_INDEX_URL="$PIP_INDEX_URL" \
    "$REPO_ROOT"
echo "[build] done: $IMAGE_TAG"
