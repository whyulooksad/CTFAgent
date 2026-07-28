# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|--------|---------|
| I1 | verified | XOR 布尔盲注: `0^(ascii(substr((select(max(flag))from(flag)),1,1))>80)` 绕过 WAF | 成功提取完整 flag | 2026-07-28T23:26 |
| I2 | pending | IF 布尔盲注: `if(ascii(substr(...))=X,1,2)` 作为 XOR 备选 | 未使用，XOR 已成功 | 2026-07-28T23:17 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| M1 | hint | 题目为 CISCN2019 华北赛区 Day2 Web1 "Hack World" 原题 | guidance.md 搜索 | 2026-07-28T23:05 |
| M2 | fact | flag 格式为 UUID: flag{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx} | 题目背景 | 2026-07-28T23:17 |
| M3 | evidence | WAF 过滤: and/or/union/空格/**/  全部返回 "SQL Injection Checked."；未过滤: ^/()/ascii/substr/if/from/select(无空格子查询) | Codex 探测 + guidance WP | 2026-07-28T23:19 |
| M4 | fact | 注入类型: 数字型(无引号闭合)，`1'` 返回 bool(false)；id=1 返回 "Do you want to be my girlfriend?" | Codex 探测 | 2026-07-28T23:19 |
| M5 | fact | 响应区分: "girlfriend" 关键词 -> TRUE；"bool(false)"/"Error" -> FALSE；"SQL Injection Checked." -> WAF 拦截 | Codex 探测 + guidance WP | 2026-07-28T23:19 |
| M6 | fact | 服务: openresty + PHP/5.6.40，入口 /index.php，POST 参数 id | HTTP response | 2026-07-28T23:18 |
| M7 | evidence | `select(flag)from(flag)` 返回多行导致 "Error Occured"；`limit` 被 WAF 拦截；改用 `max(flag)`/`min(flag)` 聚合为单行成功 | Codex 调试 | 2026-07-28T23:22 |
| M8 | fact | FLAG: `CTF2{b41594f3-9a00-4577-b9a1-780f966fb694}` (max(flag) 长度 42，min(flag) 与之一致) | XOR 盲注二分提取 | 2026-07-28T23:26 |
