# Board

## Target
- URL: http://57709ff493e994dca798fef5.http-ctf2.dasctf.com:80
- Type: web
- Start: 2026-07-31T13:15:56+08:00

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|--------|---------|
| 1 | verified | 下载 /www.tar.gz 源码包审计 | 已解压，96MB/3002文件 | 13:17 |
| 2 | verified | robots/sitemap/.git 是否真实 | 均为软200 | 13:17 |
| 3 | failed | md5sum 去重 / grep 危险函数 / HTTP离群 | 诱饵噪声，全部排除 | 13:22 |
| 4 | failed | 静态分析直连sink | 未覆写+无恒假guard=0 | 13:22 |
| 5 | failed | branch_001/002 并行分析 | 均超时无结果 | 13:23 |
| 6 | verified | bulk_probe.py 动态fuzz | **命中 xk0SzyKwfzw.php (shell RCE)** | 13:25 |
| 7 | verified | 定位参数名并读flag | **真参数: GET Efa5BVG (二分法确认), 即将读flag** | 13:27 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| 1 | fact | 技术栈: OpenResty + PHP/7.3.5 | curl 响应头 | 13:16 |
| 2 | fact | 源码3002文件: 3001 PHP+1HTML，96MB，哈希全唯一 | tar解压 | 13:17 |
| 3 | fact | 诱饵模式: 危险sink前恒假guard + $_GET覆写为空格 | 静态分析 | 13:22 |
| 4 | fact | 函数词表固定无include/base64/file | 频次统计 | 13:22 |
| 5 | fact | 直连sink 25539个，未覆写17022但全有恒假guard，可用=0 | Python分析 | 13:22 |
| 6 | evidence | **真后门: xk0SzyKwfzw.php，shell模式RCE确认(RCEX973标记回显)** | bulk_probe.py | 13:25 |
| 7 | fact | bulk_probe: 6002 jobs, 1 hit, 2 errors | /tmp/bulk_probe.out | 13:25 |
| 8 | fact | 解压目录: /tmp/ctf-src.9vgiAJ | progress.md | 13:17 |
| 9 | failure_boundary | 本机无PHP CLI | 执行php -l | 13:19 |
