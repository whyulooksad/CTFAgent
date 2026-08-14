#!/bin/bash
# 容器入口: 等价于在项目根目录直接运行 run.sh (Solver 内部零改动)。
#
# 挂载约定 (DockerBackend):
#   /root/.codex                 精制快照: auth.json / ctf.config.toml / 最小 config.toml / models_cache.json
#   /root/.hermes                精制快照: auth.json / config.yaml / .env
#   /opt/ctf-agent/challenges    题目现场 (宿主机 challenges/ 的 bind mount，删容器不删数据)
set -e
exec bash /opt/ctf-agent/run.sh "$@"
