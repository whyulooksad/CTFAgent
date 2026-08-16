# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|--------|---------|
| 1 | verified | 源码审计: source.php 暴露 emmm::checkFile 白名单(source.php/hint.php) + `?` 截断 + urldecode 分支, include 原始值 | 源码已拿到, hint 指向 ffffllllaaaagggg | 2026-08-16T16:27 |
| 2 | testing | 直接访问 /ffffllllaaaagggg (无扩展名静态文件直读) | 未验证 | 2026-08-16T16:27 |
| 3 | testing | `?` 截断绕过: source.php?/../../ffffllllaaaagggg 及 %3f urldecode 变体 | 未验证 | 2026-08-16T16:27 |
| 4 | pending | 若 include 只能读白名单文件 -> ffuf 全目录扫 upload/admin/备份 | 未验证 | 2026-08-16T16:27 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| 1 | fact | 技术栈: PHP/5.6.36 + openresty(nginx), 首页几乎空白只有一张图 | codex.log curl 首页 | 2026-08-16T16:26 |
| 2 | fact | 首页 HTML 注释含 `<!--source.php-->`, 强提示源码审计题型 | codex.log curl 首页 | 2026-08-16T16:26 |
| 3 | fact | source.php 源码: class emmm::checkFile, 白名单 [source.php, hint.php], 三重检查(原值/截断?前缀/urldecode后截断), 通过后 include $_REQUEST['file'] 原始值 | codex.log curl source.php | 2026-08-16T16:27 |
| 4 | fact | hint.php 内容: "flag not here, and flag in ffffllllaaaagggg" — flag 文件名 ffffllllaaaagggg 无扩展名 | codex.log curl hint include | 2026-08-16T16:27 |
| 5 | hint | 最短路径: 直接 curl /ffffllllaaaagggg, 无扩展名不走 PHP 解析可能直接返回 flag | Hermes guidance | 2026-08-16T16:27 |
| 6 | hint | include 对 `?` 的行为需实测: 剥离(读 source.php) vs 穿越(读 flag) vs 报错 | Hermes guidance | 2026-08-16T16:27 |
