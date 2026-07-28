# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|--------|---------|
| I-001 | confirmed | 利用 `confirm.php` 的 address 存储恶意旧地址，再由 `change.php` 触发二次 SQL 注入 | `updatexml()` 报错可分段读取 `/flag.txt`，已成功取旗 | 2026-07-29 |
| 1 | testing | php://filter 读源码 | 确认 ?file= 参数可文件包含 | 2026-07-29 00:07 |
| 2 | testing | confirm.php -> change.php address 二次注入 | 已确认 address 不过滤、change.php 用旧 address 拼接 SQL | 2026-07-29 00:07 |
| 3 | pending | updatexml 报错注入读 /flag.txt | WP 确认 flag 在 /flag.txt，直接 load_file | 2026-07-29 00:07 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| M-001 | vulnerability | `confirm.php` 的 address 不过滤；`change.php` 将数据库中的旧 address 未转义拼接进 UPDATE，并回显数据库错误 | 实测及监督者情报 | 2026-07-29 |
| M-002 | flag | `CTF2{5ce93958-db47-4e3c-a3ad-b18d76ac07e4}` | `/flag.txt` 二次注入分段读取 | 2026-07-29 |
| 1 | fact | 目标是 CISCN2019 CyberPunk 原题 (BUUCTF) | WP 搜索 | 2026-07-29 00:07 |
| 2 | fact | 技术栈: openresty + PHP/7.3.10 | curl 探测 | 2026-07-29 00:07 |
| 3 | fact | 路由: index.php, confirm.php, change.php, search.php, delete.php, config.php | curl 探测 | 2026-07-29 00:07 |
| 4 | fact | 黑名单(user_name/phone): select\|insert\|update\|delete\|and\|or\|join\|like\|regexp\|where\|union\|into\|load_file\|outfile | WP | 2026-07-29 00:07 |
| 5 | fact | address 字段在 confirm.php 完全不过滤，change.php 做 addslashes 但旧 address 直接拼接 | WP | 2026-07-29 00:07 |
| 6 | fact | change.php 有 print_r($db->error) 报错回显 | WP | 2026-07-29 00:07 |
| 7 | hint | flag 在 /flag.txt，直接 load_file('/flag.txt') 即可，无需爆库爆表 | WP (多篇) | 2026-07-29 00:07 |
| 8 | hint | updatexml 最多 32 字符，需 substr 分段读取 | WP | 2026-07-29 00:07 |
| 9 | hint | 每个 payload 需新订单（注入破坏 old_address，同订单无法换 payload） | WP | 2026-07-29 00:07 |
| 10 | fact | 数据库配置: host=127.0.0.1, user=root, pass=root, db=ctfusers | config.php (WP) | 2026-07-29 00:07 |
