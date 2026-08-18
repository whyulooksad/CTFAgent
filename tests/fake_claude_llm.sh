#!/bin/bash
# fake_claude_llm.sh -- tests/test_master.py 用的假 claude。
# 从 prompt 中提取 id=xxx，按 FAKE_MODE 决定行为:
#   ok       倒序输出合法 JSON 数组 (验证有效重排路径)
#   garbage  输出非 JSON (验证非法输出回退)
#   fail     直接退出非零 (验证调用失败回退)
# 调用形态: fake_claude_llm.sh -p "<prompt>" --tools "" --no-session-persistence

set -u
MODE="${FAKE_MODE:-ok}"

if [ "$MODE" = "fail" ]; then
  echo "fake claude: boom" >&2
  exit 1
fi

if [ "$MODE" = "garbage" ]; then
  echo "fake claude text"
  echo "我觉得这些题目都很简单，随便做。"
  exit 0
fi

# ok: 提取 id 并倒序
PROMPT=""
for arg in "$@"; do :; done
PROMPT="$2"   # -p 后第一个参数即 prompt
IDS=$(printf '%s' "$PROMPT" | grep -oE 'id=[^ |]+' | sed 's/^id=//' \
      | awk '{a[NR]=$1} END{for(i=NR;i>=1;i--) printf "\"%s\",", a[i]}')
echo "[${IDS%,}]"
