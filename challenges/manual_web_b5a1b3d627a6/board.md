# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|---------|--------|
| I1 | verified | 首页源码与技术栈侦察 | 根路径直接高亮 PHP 源码；后端 PHP 7.3.15，存在 payload 反序列化入口 | 2026-07-31T18:53:26+08:00 |
| I2 | testing | PHP 对象反序列化 gadget：__destruct echo 对象触发 __toString 后 exec | Codex 正以嵌套 minipop 与 sleep 时间侧信道验证执行 | 2026-07-31T18:53:26+08:00 |
| I3 | pending | 无回显命令执行的 flag 外带/盲取 | 过滤规则下可考虑反引号、cat、cut、od、wc 与时间侧信道 | 2026-07-31T18:53:26+08:00 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| M1 | fact | 目标为 http://da3ad6c0779bc43e7d2b1407.http-ctf2.dasctf.com:80；题目类型 web，未提供背景。 | progress.md | 2026-07-31T18:52:40+08:00 |
| M2 | fact | 根路径源码显示 POST[payload] 经 unserialize()；minipop 含 code、qwejaskdjnlka，析构 echo 后者；__toString 对 code 通过过滤后 exec 并返回 alright。 | progress.md | 2026-07-31T18:53:26+08:00 |
| M3 | fact | 过滤 `$ . ! @ # % ^ & * ? { } > <`，以及 nc、tee、wget、exec、bash、sh、netcat、grep、base64、rev、curl、gcc、php、python、pingtouch、mv、mkdir、cp（大小写不敏感）。 | progress.md | 2026-07-31T18:53:26+08:00 |
| M4 | evidence | Codex 构造外层 qwejaskdjnlka 指向内层 minipop；内层 code=sleep 3，以时间侧信道验证 gadget。 | codex.log | 2026-07-31T18:53:26+08:00 |
