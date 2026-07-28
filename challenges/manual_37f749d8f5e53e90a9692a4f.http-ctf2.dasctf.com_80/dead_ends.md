# Dead Ends (硬约束 -- 禁止重试)

## DE1: 传统联合/布尔/报错注入 (and/or/union + 空格)
- 方向: 用 `and`/`or`/`union`/`空格` 做联合/布尔/报错注入
- 原因: WAF 过滤 and/or/union/空格/**/，全部返回 "SQL Injection Checked."
- 证据: Codex 探测 8 种 payload 全部被拦截 (1 and 1=1, 1 or 1=1, 1 union select 1, 1' or '1'='1 等)
- 时间: 2026-07-28T23:19
- 替代方案: 用 `^`(XOR) + `()` 代替空格，见 guidance.md

## DE2: 时间盲注 (sleep + and/or)
- 方向: `1 and sleep(2)` 时间盲注
- 原因: `and` 被过滤，返回 "SQL Injection Checked."
- 证据: Codex 探测 `1 and sleep(2)` 被拦截
- 时间: 2026-07-28T23:19
- 替代方案: 如需时间盲注可用 `if(条件,sleep(2),0)` (不带 and/or)，但布尔盲注更高效
