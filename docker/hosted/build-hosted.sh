#!/bin/bash
# 构建托管模式镜像 ctf-solver-hosted
# 用法: bash docker/hosted/build-hosted.sh [镜像名]   (默认 ctf-solver-hosted)
set -euo pipefail

cd "$(dirname "$0")/../.."   # 仓库根
IMAGE="${1:-ctf-solver-hosted}"

# 1. 取当前快照真实路径 (current 是软链, docker COPY 不跟)
SNAP=$(readlink -f cred_snapshots/current)
echo "[build-hosted] 快照: $SNAP"

# 2. 复制快照到构建上下文 (cred_snapshots/ 被 .dockerignore 排除)
rm -rf docker/hosted/snap_build
mkdir -p docker/hosted/snap_build
cp -r "$SNAP/codex" docker/hosted/snap_build/codex
cp -r "$SNAP/hermes" docker/hosted/snap_build/hermes
trap 'rm -rf docker/hosted/snap_build' EXIT

# 3. 重新生成 claude settings (宿主 ~/.claude/settings.json → 镜像内)
python3 - <<'PY'
import json, os
src = json.load(open(os.path.expanduser('~/.claude/settings.json')))
env = src.get('env', {})
json.dump({'env': env}, open('docker/hosted/claude-settings.json', 'w'), indent=2)
print('[build-hosted] claude-settings.json 已更新')
PY

# 4. 构建 (基础镜像 ctf-solver:latest 需是最新: 先 build.sh)
bash docker/solver/build.sh >/dev/null 2>&1 || true

# 5. 构建托管镜像
docker build -f docker/solver/Dockerfile.hosted -t "$IMAGE" .
echo "[build-hosted] done: $IMAGE"

# 6. 导出 (上传平台用)
docker save "$IMAGE" | gzip > "${IMAGE}.tar.gz"
echo "[build-hosted] 已导出: ${IMAGE}.tar.gz ($(du -h "${IMAGE}.tar.gz" | cut -f1))"
