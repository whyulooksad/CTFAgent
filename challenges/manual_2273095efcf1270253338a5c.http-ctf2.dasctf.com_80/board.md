# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|--------|---------|
| 1 | failed | 登录 SQL 注入 (username/password) | branch_001 INFEASIBLE，10 载荷无差异 | 01:08 |
| 2 | failed | Spring Boot/Thymeleaf 攻击面 | 后端实为 Flask，Thymeleaf 是误导 | 01:08 |
| 3 | verified | Flask 404 页 Jinja2 SSTI | 确认 SSTI，已绕过全部过滤获得 RCE | 01:08 |
| 4 | failed | sudo 提权 | nosuid 禁用，sudo 不可用 | 01:08 |
| 5 | failed | su dragon_lord 弱口令 | 空密码/dragon_lord/94608000 全失败 | 01:08 |
| 6 | failed | os.setuid(1001) 直接提权 | 进程无 CAP_SETUID，返回 500 | 01:08 |
| 7 | testing | SUID/SGID 二进制枚举 | 尚未检查！Linux 提权第一优先级 | 01:08 |
| 8 | testing | Linux Capabilities 枚举 | 尚未检查！cap_dac_read_search/cap_setuid | 01:08 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| 1 | fact | 目标: http://2273095efcf1270253338a5c.http-ctf2.dasctf.com:80 | progress.md | 01:08 |
| 2 | evidence | Flask 404 SSTI：黑名单 = request/%/./join/数字；用 dict(key=x)|first 造字符串、|attr() 绕过点号、长度算术生成数字 | codex.log | 01:08 |
| 3 | evidence | subprocess 被 handler 替换为字符串；用 __builtins__.__import__("sys").modules.pop("subprocess") 恢复后 os.popen 可用 | codex.log | 01:08 |
| 4 | evidence | RCE 身份: uid=1000(zhuixu) gid=1000(zhuixu)；/flag.txt mode 0700 owner dragon_lord(1001) | codex.log | 01:08 |
| 5 | failure_boundary | sudo 不可用(nosuid)；su dragon_lord 三次密码全失败；os.setuid(1001) 无 CAP_SETUID 失败 | codex.log | 01:08 |
| 6 | fact | hint: "become dragon_lord. wait 3 years? Or do dragon_lord's service by yourself" | progress.md | 01:08 |
| 7 | fact | Wait_3_years 脚本: echo+sleep 94608000+/bin/bash；mode 0744 owner dragon_lord 无 setuid | codex.log | 01:08 |
| 8 | fact | dragon_lord 仅拥有: home dotfiles + Wait_3_years + /flag.txt；无隐藏 service/二进制 | codex.log | 01:08 |
| 9 | fact | 进程: PID1/7 root /start.sh, PID8 root sudo -u zhuixu python, PID9 zhuixu；无 dragon_lord 进程 | codex.log | 01:08 |
| 10 | hint | 未检查: SUID二进制(find / -perm -4000)、capabilities(getcap -r /)、其他cron位置、可写文件 | guidance.md | 01:08 |
| 11 | external | cap_dac_read_search 可读任意文件；cap_setuid 可改UID；python3带cap_setuid可os.setuid提权 | anysearch | 01:08 |
| 12 | external | GTFOBins: python3 SUID -> os.execl("/bin/sh","sh","-p"); find SUID -> -exec /bin/sh -p | anysearch | 01:08 |
