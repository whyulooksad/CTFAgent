## Target
- Type: web
- URL: http://57709ff493e994dca798fef5.http-ctf2.dasctf.com:80
- Background: 
- Start Time: 2026-07-31T13:15:56+08:00

## Current Phase
solved

## Next Steps
1. 输出最终结构化 JSON

## Key Artifacts
- `/tmp/ctf_home.txt`: 首页完整响应；OpenResty + PHP/7.3.5
- 首页提示网站源码备份位于 `/www.tar.gz`
- `/tmp/www.tar.gz`: 有效 gzip，41,300,918 bytes，解压后约 96MB；含大量随机名 PHP
- robots/sitemap/.git 均为首页统一软 200，不是真实资源
- `/tmp/ctf-src.9vgiAJ`: 安全解压目录；共 3002 文件（3001 PHP + 1 HTML），96MB
- 首次调用 `python3 branch.py` 失败：挑战目录无该脚本，需在上层定位
- 已定位 daemon 客户端：`/home/stw/ctf-agent/branch.py`
- 已启动异步分支：branch_001 dangerous_sink_scan、branch_002 code_clustering
- 所有 3001 个 PHP 内容哈希均唯一；唯一 HTML 仅为当前首页
- PHP 均含大量随机 `$_GET`/`$_POST` 与 eval/assert/system/exec token，简单 grep 信噪比极低
- 明文 flag 命中均为随机标识符/参数名，暂未见 `flag{...}`
- 本机未安装 PHP CLI；`/tmp/php_invalid.txt` 结果无效（全部仅因 `php: command not found`）
- 抽样请求 8 个随机 PHP 均为 200，响应约 0.8–3KB 随机调试输出，说明文件在线且可执行
- 全量 HTTP 响应采集正在 session 13795 中运行（24 并发，逐文件 12s 超时）
- 全量采集完成：3000 个 HTTP 200，1 个超时；响应目录 `/tmp/ctf-resp.qoWGAe`，元数据 `/tmp/ctf_http_meta.txt`
- 唯一超时文件 `ASrs5FZnWZi.php`；27 个响应含随机 flag/ctf 子串，待精查
- 响应内 flag/ctf 命中均为随机字符串，未见 FLAG 格式
- `ASrs5FZnWZi.php` 源码含顶层 `$_GET['BfgA7IHwU']=' '; ... assert($_GET['BfgA7IHwU'] ?? ' ');`，但先主动覆盖输入为空格，疑似诱饵/干扰；需检查其它危险调用
- 验证 ASrs 的 `ol_4MNx_Q` 也在执行前被覆写为空格；phpinfo/system/id/cat flag 均无执行，只有 assert 空格失败警告
- ASrs 首次超时为瞬态网络/服务抖动，后续约 0.3s 正常返回 2092 bytes，不再视为真实入口
- 全语料函数调用几乎仅由生成器固定词汇组成：var_dump/stdClass/explode/print_r/preg_match/function_exists/str_replace/preg_replace/eval/assert/exec/system；无 base64/file/include 等独特后门词
- 静态直连 sink 共 25,539 个；17,022 个未被同键覆写，但全部受明显恒假字符串比较保护，其余均在 sink 前被覆写
- 说明真实漏洞更可能经变量传递或特殊 preg_replace 数据流，需动态批量差分
- `bulk_probe.py`: 为每个 PHP 收集全部 GET/POST 键，分别注入无明文标记的 shell/PHP 执行探针并检测解码后的 `RCEX973`
- 动态全参数差分扫描正在 session 9484 运行；输出 `/tmp/bulk_probe.out`，进度/错误 `/tmp/bulk_probe.err`
- branch_001 与 branch_002 均到 300s 超时，结果文件已生成，待读取
- 两个分支均未在超时前写出内容（results content=null），主线继续独立分析
- session 9484 已结束，需读取 `/tmp/bulk_probe.{out,err}` 确认命中
- 动态扫描唯一命中：`xk0SzyKwfzw.php` 的 shell 探针返回解码标记 `RCEX973`（HTTP 200, 991 bytes）；已确认存在 shell 命令执行
- 该文件仅 435 行/11KB，显著候选为第 270 行 `@preg_replace("/x3KITvg8DdZ/e", $_GET['kBVLzQEgb'] ?? ' ', 'CtIBLNO__')`
- 另有硬编码字符串经 `eval($CuIHLOyVU)` 执行，但字符串内容仅含随机赋值/输入读取；需以动态单键结果为准
- 单独注入 `GET kBVLzQEgb` 未命中 marker；该 `/e` 调用因固定 pattern 不匹配固定 subject，属于诱饵
- 对 27 个参数稳定二分到唯一触发键 `GET Efa5BVG`；该键单独注入即返回 `RCEX973`
- 源码确认动态调用：拼接 `"sY"."stEmXnsTcx"` 后按 `Xn` 分割得到 `sYstEm`（PHP 函数名大小写不敏感，即 `system`），再执行 `($kDxfM->gHht)($_GET['Efa5BVG'])`
- `id` 成功：RCE 身份为 `uid=82(www-data) gid=82(www-data)`
- `cat /flag*` 无输出，flag 不在根目录常规文件名
- `find` 实际定位到 `/flag`，`ls -l` 显示 43 bytes、0644、root:root；之前 `cat /flag*` 无输出需单独复验
- 环境变量 `FLAG=not_flag` 明确为诱饵
- 已通过 `cat /flag`、`base64 /flag`、`od -An -tx1 /flag` 三种方式交叉验证真实 flag
- branch_001 已读取 board/progress；当前开始对 3001 个 PHP 做危险 sink 与用户输入的系统化静态扫描
- 已确认 Web 策略文件位于 `/home/stw/ctf-agent/strategies/web.md`，待按其流程执行
- branch_001 初扫结果：危险命令 sink 32,290 行、include/反序列化类 9,395 行、用户输入 264,901 行；高密度随机诱饵明显，下一步按每文件 sink 数、首行可达性和 PHP 7.3 兼容性排序
- 已确认典型诱饵形态：危险调用前置恒假常量比较（如 `'Q6kMBHJG2' == 'RD1EgknVG'`）；后续筛选将重点找恒真/无 guard 且不在注释中的 sink
- 可达性初筛未发现恒真常量 guard，但发现大量无简单 guard 命中；其中不少在 sink 前把同名 `$_GET` 覆写为空，需继续做同参数定义追踪并优先验证靠前、未覆写的命令 sink
- 同参数全文件追踪显示：所有直接 `system/exec/eval($_GET/POST[...])` 在此前都出现过对同名输入槽的赋值，说明真正异常更可能是“用户输入→局部变量→sink”链或赋值处位于不可达分支
- 首版局部变量污点脚本因逐变量正则复杂度过高已中止，未产生结论；改为只维护变量布尔污点并通过一次 RHS 变量提取传播

## Flags Found
- `CTF2{825c74dc-e58e-4a9c-83e7-74b823140b85}`
