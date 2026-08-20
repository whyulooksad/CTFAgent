#!/bin/bash
# 构建托管模式镜像 ctf-solver-hosted
# 用法: bash docker/hosted/build-hosted.sh [镜像名]   (默认 ctf-solver-hosted)
#
# 安全要求 (平台): 镜像内不包含任何密钥/凭证/大模型 API key
#   - codex config.toml  / hermes .env / claude settings.json 的 key 一律替换为 {{DEEPSEEK_API_KEY}} 占位符
#   - hermes 历史会话 (state.db / sessions/) 不打包
#   - 运行时由平台页面配置环境变量 DEEPSEEK_API_KEY, entrypoint-hosted.sh 注入
set -euo pipefail

cd "$(dirname "$0")/../.."   # 仓库根
IMAGE="${1:-ctf-solver-hosted}"

# 1. 取当前快照真实路径 (current 是软链, docker COPY 不跟)
SNAP=$(readlink -f cred_snapshots/current)
echo "[build-hosted] 快照: $SNAP"

# 1b. 校验快照是 deepseek 托管配置 (防止宿主机 cred_snapshot 重新生成
#     其它 provider 快照覆盖 current 导致构建错镜像; 托管用 deepseek 网关)
if ! grep -q 'provider: deepseek' "$SNAP/hermes/config.yaml" 2>/dev/null; then
    echo "[build-hosted] !! 错误: current 快照不是 deepseek 配置 (hermes config.yaml 无 'provider: deepseek')" >&2
    echo "[build-hosted] !! 请恢复 deepseek 快照后重试" >&2
    exit 1
fi
echo "[build-hosted] 快照校验通过: deepseek 托管配置"

# 2. 复制快照到构建上下文并脱敏 (cred_snapshots/ 被 .dockerignore 排除)
rm -rf docker/hosted/snap_build
mkdir -p docker/hosted/snap_build
cp -r "$SNAP/codex" docker/hosted/snap_build/codex
cp -r "$SNAP/hermes" docker/hosted/snap_build/hermes
trap 'rm -rf docker/hosted/snap_build' EXIT

# 2a. codex: API key → 占位符
sed -i 's|^experimental_bearer_token = ".*"|experimental_bearer_token = "{{DEEPSEEK_API_KEY}}"|' \
    docker/hosted/snap_build/codex/config.toml

# 2b. hermes: 剔除历史会话, .env 的 key → 占位符
# 注意: bin/tirith (hermes 安全扫描二进制) 必须保留! 缺失时 hermes 首次
# terminal 调用会现场下载 GitHub release, 托管沙箱无公网 → 必崩 (实测 2026-08-18)
rm -f docker/hosted/snap_build/hermes/state.db
rm -rf docker/hosted/snap_build/hermes/sessions
rm -f docker/hosted/snap_build/hermes/.skills_prompt_snapshot.json
rm -rf docker/hosted/snap_build/hermes/skills/.curator_backups   # hermes 技能旧备份
sed -i 's|^DEEPSEEK_API_KEY=.*|DEEPSEEK_API_KEY={{DEEPSEEK_API_KEY}}|' \
    docker/hosted/snap_build/hermes/.env
# subapi (本地专用) 等其它 key 一律清空, 托管只用 deepseek 网关
sed -i 's|^OPENAI_API_KEY=.*|OPENAI_API_KEY=|' \
    docker/hosted/snap_build/hermes/.env
# 兜底: skills 文档若残留真实 key (sk- 前缀或点号分隔 key 格式, 文档词如 *** 不会命中) → 占位
grep -rlE "sk-[A-Za-z0-9]{25,}|[0-9a-f]{32}\.[A-Za-z0-9]{20,}" docker/hosted/snap_build 2>/dev/null | while read -r f; do
    sed -i -E "s|sk-[A-Za-z0-9]{25,}|{{DEEPSEEK_API_KEY}}|g; s|[0-9a-f]{32}\.[A-Za-z0-9]{20,}|{{DEEPSEEK_API_KEY}}|g" "$f"
done

# 2c. claude settings: 占位符模板 (不复制宿主真实 key)
cat > docker/hosted/claude-settings.json << 'EOF'
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "{{DEEPSEEK_API_KEY}}",
    "ANTHROPIC_MODEL": "deepseek-v4-pro"
  }
}
EOF
echo "[build-hosted] 脱敏完成 (key → {{DEEPSEEK_API_KEY}}, 历史会话已剔除)"

# 3. 确认脱敏后无真实 key 残留 (sk- 前缀或点号分隔 key 格式; "Task-specific" 等文档子串不算)
if grep -rEq "sk-[A-Za-z0-9]{25,}|[0-9a-f]{32}\.[A-Za-z0-9]{20,}" docker/hosted/snap_build docker/hosted/claude-settings.json 2>/dev/null; then
    echo "[build-hosted] !! 错误: 快照仍有真实 key 残留, 终止构建" >&2
    grep -rlE "sk-[A-Za-z0-9]{25,}|[0-9a-f]{32}\.[A-Za-z0-9]{20,}" docker/hosted/snap_build docker/hosted/claude-settings.json 2>/dev/null | head -5 >&2
    exit 1
fi
echo "[build-hosted] 安全检查通过: 无真实 key"

# 4. 构建 (基础镜像 ctf-solver:latest 需是最新: 先 build.sh)
bash docker/solver/build.sh >/dev/null 2>&1 || true

# 5. 构建托管镜像
docker build -f docker/solver/Dockerfile.hosted -t "$IMAGE" .
echo "[build-hosted] done: $IMAGE"

# 6. 导出 (上传平台用)
docker save "$IMAGE" | gzip > "${IMAGE}.tar.gz"
echo "[build-hosted] 已导出: ${IMAGE}.tar.gz ($(du -h "${IMAGE}.tar.gz" | cut -f1))"
