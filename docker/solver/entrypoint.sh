#!/bin/bash
# 容器入口: 等价于在项目根目录直接运行 run.sh (Solver 内部零改动)。
#
# 挂载约定 (DockerBackend):
#   /root/.hermes                精制快照: auth.json / config.yaml / .env / skills/
#   /opt/ctf-agent/challenges    题目现场 (宿主机 challenges/ 的 bind mount，删容器不删数据)
#   /opt/ctf-agent/llm.yaml      赛方大模型平台接入 (存在才挂; claude code 经环境变量接入)
set -e
exec bash /opt/ctf-agent/solver/run.sh "$@"
