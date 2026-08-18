#!/bin/bash
# CTF 运行监控: 新 flag / 容器崩溃 / codex 停滞 / hermes 死锁
# 无异常时静默(空输出), 有异常才打印 —— 配合 cronjob no_agent 模式
STATE=/tmp/ctf-watch-state
mkdir -p "$STATE"
cd /home/stw/ctf-agent || exit 1

OUT=""
NOW=$(date '+%H:%M:%S')

# 1. master.log 新增关键事件
LAST=$(cat "$STATE/master_lines" 2>/dev/null || echo 0)
TOTAL=$(wc -l < master.log 2>/dev/null || echo 0)
if [ "$TOTAL" -gt "$LAST" ]; then
  NEW=$(tail -n +$((LAST+1)) master.log)
  EVENTS=$(echo "$NEW" | grep -E "FLAG ACCEPTED|correct|ERROR|Traceback|终态|重试|分发|kill" | tail -12)
  [ -n "$EVENTS" ] && OUT="$OUT
[$NOW master] $EVENTS"
  echo "$TOTAL" > "$STATE/master_lines"
fi

# 2. 容器崩溃 (Exited 的 solver 容器)
DEAD=$(docker ps -a --filter "status=exited" --format '{{.Names}} {{.Status}}' 2>/dev/null | grep solver)
[ -n "$DEAD" ] && OUT="$OUT
[$NOW 容器退出] $DEAD"

# 3. codex.log 停滞 (容器在跑但 log 5 分钟没动)
for d in challenges/manual_*; do
  [ -d "$d" ] || continue
  LOG="$d/codex.log"
  [ -f "$LOG" ] || continue
  AGE=$(( $(date +%s) - $(stat -c %Y "$LOG") ))
  if [ "$AGE" -gt 300 ]; then
    OUT="$OUT
[$NOW 停滞] $(basename $d) codex.log ${AGE}s 未更新"
  fi
done

# 4. hermes 活跃度 (hermes.log 10 分钟没动 = 疑似死锁/缺席)
for d in challenges/manual_*; do
  [ -d "$d" ] || continue
  HL="$d/hermes.log"
  if [ -f "$HL" ]; then
    HAGE=$(( $(date +%s) - $(stat -c %Y "$HL") ))
    [ "$HAGE" -gt 600 ] && OUT="$OUT
[$NOW hermes静默] $(basename $d) ${HAGE}s"
  else
    OUT="$OUT
[$NOW hermes无日志] $(basename $d)"
  fi
done

[ -n "$OUT" ] && echo "$OUT"
exit 0
