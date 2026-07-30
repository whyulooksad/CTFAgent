# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|--------|---------|
| 1 | verified | 查看 `?action:index;True%23False` 获取源码 | 取得完整源码 | 02:07 |
| 2 | verified | 用 trigger_event 链式调用 buy+get_flag 绕过点数检查 | 成功获取 flag | 02:09 |
| 3 | verified | 分析 action 队列执行顺序，确认 consume_point 位置 | buy 先加 diamonds 再排队 consume，可利用 trigger_event 注入提前事件 | 02:09 |
| 4 | verified | eval 注释绕过: action=trigger_event# 使 eval("trigger_event#_handler") 实际取 trigger_event | branch_001 成功利用 | 02:09 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| 1 | fact | 目标是 Flask/Python2 Task 型 CTF，action 参数经 eval() 处理 | codex.log + 源码 | 02:07 |
| 2 | fact | URL 模式: `?action:NAME;ARG`，`#` 分隔多 action，`;` 分隔名和参数 | 首页链接 | 02:07 |
| 3 | fact | 前置 openresty，后端 Flask/Werkzeug | progress.md | 02:07 |
| 4 | evidence | 首页: "0 diamonds, 3 points"，4 个入口 | codex.log | 02:07 |
| 5 | external | 匹配 writeup: trigger_event 链 buy+get_flag | anysearch | 02:07 |
| 6 | fact | 字符白名单: 字母数字 `_ : ; #`，长度 ≤100；`#` 在 eval 中做 Python 注释 | 源码分析 | 02:08 |
| 7 | fact | buy 先加 diamonds 再排队 consume_point；不足触发 RollBack 恢复 num_items/points | 源码 | 02:08 |
| 8 | fact | get_flag 在 diamonds>=5 时排队 `func:show_flag;<FLAG>`，但 show_flag 返回诱饵 | 源码 | 02:08 |
| 9 | evidence | session['log'] 保存最近 5 个事件且回滚时不恢复 log -> flag 泄露面 | 源码分析 | 02:09 |
| 10 | evidence | 利用链: `?action:trigger_event#;action:buy;5#action:get_flag;` -> 回滚但 log 留 flag | branch_001 exploit | 02:09 |
| 11 | fact | FLAG: CTF2{48601e0a-21a7-4d76-9187-666522614422} | session.log 解码 | 02:09 |
| 12 | fact | 主线独立复现 branch_001 结果，双重验证 | codex.log | 02:09 |

## Result

- Solved: true
- Flag: CTF2{48601e0a-21a7-4d76-9187-666522614422}
- 耗时: ~2 分钟
- 关键: eval 中 `#` 注释绕过 + trigger_event 注入事件队列 + session.log 回滚不恢复
