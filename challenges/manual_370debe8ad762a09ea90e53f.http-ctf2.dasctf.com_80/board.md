# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|--------|---------|
| 1 | pending | Base32 解码 GFXEIM3YFZYGQ4A= -> 1nD3x.php | 待 Codex 执行 | 2026-07-30T23:46 |
| 2 | pending | 访问 /1nD3x.php 获取 PHP 源码 | 待 Codex 执行 | 2026-07-30T23:46 |
| 3 | pending | 6 层绕过 + create_function 注入 | 参考 reference_solve.py | 2026-07-30T23:46 |
| 4 | pending | require + ~() 取反读取 rea1fl4g.php | 真flag在此文件 | 2026-07-30T23:46 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| 1 | external | 题目是 [BJDCTF2020]EzPHP，经典代码审计题 | anysearch WP 搜索 | 2026-07-30T23:46 |
| 2 | fact | GFXEIM3YFZYGQ4A= 是 Base32，解码得 1nD3x.php | 多篇 WP 确认 | 2026-07-30T23:46 |
| 3 | fact | PHP 源码有 6 层过滤：QUERY_STRING/$_REQUEST/file_get_contents/sha1/extract/create_function | WP 分析 | 2026-07-30T23:46 |
| 4 | hint | 真 flag 在 rea1fl4g.php，假 flag 在 flag.php (BJD{1am_a_fake_f41111g23333}) | WP 确认 | 2026-07-30T23:46 |
| 5 | hint | Layer1 绕过：URL 编码所有参数名和值 | QUERY_STRING 原始匹配 | 2026-07-30T23:46 |
| 6 | hint | Layer4 绕过：sha1 传数组，NULL===NULL | 数组绕过强比较 | 2026-07-30T23:46 |
| 7 | hint | Layer6 绕过：create_function 注入 + ~() 取反绕关键字过滤 | create_function('', $arg) | 2026-07-30T23:46 |
| 8 | fact | Server: openresty，禁用右键 (oncontextmenu) | curl 探测 | 2026-07-30T23:46 |
| 9 | external | 完整参考脚本已写：reference_solve.py | Hermes 写入 | 2026-07-30T23:46 |
