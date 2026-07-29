# CTF 看板

## Target
- 题目类型: misc
- 附件: 111.pcap (pcap capture file, DLT_NULL/loopback 封装)
- 背景: 分析出黑客获取的机密文件内容

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|--------|---------|
| 1 | verified | 解析 pcap 中的 HTTP 流量，还原盲注提取的数据 | flag{c84bb04a-8663-4ee2-9449-349f1ee83e11} | 2026-07-29T21:12 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| 1 | fact | pcap 是 DLT_NULL 封装（loopback 抓包），4字节头+IP | file 命令 | 2026-07-29T21:12 |
| 2 | evidence | 攻击者对 /comments.php?name= 做布尔盲注，提取 wfy_comments 表 id=100 的 text 字段 | strings 分析 | 2026-07-29T21:12 |
| 3 | evidence | 注入 payload: if((substr((select(text)from(wfy_comments)where(id=100)),POS,1)="CHAR"),100,0) | strings 分析 | 2026-07-29T21:12 |
| 4 | fact | 攻击者字符集: qwertyuioplkjhgfdsazxcvbnm-_1234567890（不含大括号） | 正则提取 | 2026-07-29T21:12 |
| 5 | fact | 共 49 个位置被测试，实际字符串 42 字符（位置43-49超长无匹配） | 正则提取 | 2026-07-29T21:12 |
| 6 | evidence | 位置5和42均测试全部26小写字母无匹配 -> 是 { 和 } | 脚本分析 | 2026-07-29T21:12 |
| 7 | external | 同类 CTF 题目: 布尔盲注流量还原，提取攻击者获取的数据 | anysearch 搜索 | 2026-07-29T21:12 |

## Flags Found

flag{c84bb04a-8663-4ee2-9449-349f1ee83e11}
