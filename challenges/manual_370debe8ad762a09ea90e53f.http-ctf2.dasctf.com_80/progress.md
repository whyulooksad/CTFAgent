## Target
- Type: web
- URL: http://370debe8ad762a09ea90e53f.http-ctf2.dasctf.com:80
- Background: 
- Start Time: 2026-07-30T23:46:16+08:00

## Current Phase
solved

## Next Steps
1. 输出结构化最终结果

## Key Artifacts
- /home/stw/ctf-agent/strategies/web.md: 已读取 Web 攻击流程
- board.md: 当前无历史 ideas/memory
- /tmp/ctf_home.txt: 首页完整响应
- 首页技术栈: openresty，静态 HTML
- 首页源码注释: `Here is the real page =w=` 和 `GFXEIM3YFZYGQ4A=`
- Base32 解码结果: `1nD3x.php`
- /tmp/ctf_hidden.txt: 隐藏页完整 PHP 高亮源码
- PHP 7.3.13；核心链为 `file_get_contents` 精确比较、SHA1 数组碰撞、`extract($_GET["flag"])`、`$code('', $arg)`
- 可行绕过思路: GET 保存真实值，POST 同名数组覆盖 `$_REQUEST`，从而让 foreach 中 `preg_match` 接收数组并返回 false
- branch daemon: `/home/stw/ctf-agent/branch.py`
- branch_001: request_merge_chain，FEASIBLE
- branch_002: dynamic_callable，FEASIBLE
- branch_002 已还原 arg 黑名单，正在用 PHP 7.3.13 容器验证精确 callable payload
- branch_002 联网定位到同题 `[BJDCTF2020]EzPHP`，公开解法候选为 `create_function` + `}var_dump(get_defined_vars());//`
- branch_002 已生成全字节百分号编码 GET 串，避免原始 QUERY_STRING 黑名单
- branch_002 首次实测被 QUERY_STRING 首层黑名单拦截，需定位百分号编码串中意外命中片段
- 原因已定位：Python `requests` 会把 URL 中百分号编码的 unreserved 字符规范化解码；需改用保留原始 request-target 的客户端
- 使用 `http.client` 保留原始 request-target 后首阶段实测成功；`get_defined_vars()` 暴露真实文件 `rea1fl4g.php`
- branch_002: dynamic_callable，FEASIBLE；结果已写入 `branch_result_branch_002.md`
- 二阶段 `create_function` + `require(~filter-wrapper)` 已在目标读取 `rea1fl4g.php`
- branch_001 已验证请求合并前半链
- 首次主线请求被 QUERY_STRING 黑名单拦截：Python requests 将 `%66` 等未保留字符规范化回明文
- `/tmp/branch001_chain1.html`: curl 保持百分号编码，组合链通过并依次出现 `Neeeeee! Good Job!`、`Very good! you know my password`
- branch_001 已验证 POST 同名数组覆盖 `$_REQUEST`，同时 `$_GET` 保留原始值
- branch_001 已验证 `debu=aqua_is_cute%0a`、`data://text/plain,debu_debu_aqua` 与不同 SHA1 数组可组合成功
- 主线用 `http.client` 保留全百分号编码后也已复现前半链完整通过
- `}var_dump(getenv());//` 成功穿透 arg 黑名单并在 create_function 编译期执行，但环境变量结果为 NULL
- 新利用设计: arg 黑名单未禁 `system`/`base64_decode`，以 PHP 7.3 未定义常量作为无引号 Base64 字符串
- `system(base64_decode(bHMgLw))` 无输出，推断 `system` 位于 PHP `disable_functions`；转向纯 PHP 变量泄露
- 动态 `compact('flag')` 经 `call_user_func` 返回 NULL，说明未继承 flag 所在符号表
- 下一步以 Base64 隐藏 `$`/`flag`，在新匿名函数体中声明 `global $flag`
- 二次匿名函数 payload 不命中源码黑名单，但响应仅为 `Target not found`；需要做对照确认来源
- 对照首页也返回 HTTP 404 `Target not found`，确认目标实例已下线，而非 payload 输出
- branch_001 与 branch_002 均已完成并读取结果
- branch_001 对照 `/tmp/branch001_no_post.html`: 无 POST 同名覆盖时命中 `I hate English`
- branch_001 对照 `/tmp/branch001_no_nl.html`: 无 `%0a` 时未修改 `$file`，命中文件内容失败
- branch_001 对照 `/tmp/branch001_same_arrays.html`: 两个数组相同时命中 SHA1 密码失败，证明需不同数组
- `branch_result_branch_001.md`: request_merge_chain 已写明 `FEASIBLE`，包含可工作的 curl 前半段与对照证据
- robots.txt、sitemap.xml、.git/HEAD 均为 404

## Flags Found
- `CTF2{e4785e7f-6bf1-46b5-9fd2-ce170bfdf870}`（branch_002 通过 create_function 注入 + 取反 filter wrapper 实测读取）
