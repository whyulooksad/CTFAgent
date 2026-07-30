## Branch Result
direction: suid_sgid
subagent_id: branch_003
status: INFEASIBLE

### 发现
- 远端 RCE 身份为 `uid=1000(zhuixu) gid=1000(zhuixu) groups=1000(zhuixu)`。
- 全文件系统共发现 16 个 SUID/SGID 普通文件，全部是系统标准程序；没有 owner 或 group 为 `dragon_lord`（uid/gid 1001）的异常 SUID/SGID 文件。
- `dragon_lord` 为 uid/gid 1001；`/flag.txt` 是 `0700 dragon_lord:dragon_lord`。当前 uid 1000 既不是 owner，也不在 gid 1001 中。
- 决定性约束：SSTI RCE 进程的 `/proc/self/status` 显示 `NoNewPrivs: 1`，且 `CapPrm`、`CapEff` 均为 0。Linux 在 `no_new_privs` 生效时不会因 `execve` 的 setuid/setgid 位增加权限。
- 实测执行 mode 4755 的 `/usr/bin/sudo` 后，sudo 明确报告 effective uid 不是 0，证明该 RCE 上下文中的 SUID 位实际未带来提权。
- 因此清单中的 `sudo`、`su`、`passwd`、`chsh`、`chfn`、`gpasswd`、`newgrp`、`mount` 等均只能以 uid 1000/原 gid 运行；即使存在面向特权执行态的旧版本利用，它们在当前 `NoNewPrivs: 1` 执行链中也拿不到预期的 euid/egid。

### 命令和结果

身份验证：

```sh
python3 ssti_rce.py id
```

```text
uid=1000(zhuixu) gid=1000(zhuixu) groups=1000(zhuixu)
```

全文件系统 SUID/SGID 枚举（未使用 `-xdev`）：

```sh
python3 ssti_rce.py 'find / -type f -perm /6000 -ls 2>/dev/null'
```

```text
802117     40 -rwxr-sr-x 1 root shadow  39616 Feb 14  2019 /sbin/unix_chkpwd
810620    428 -rwsr-xr-x 1 root root   436552 Oct  6  2019 /usr/lib/openssh/ssh-keysign
802203     84 -rwsr-xr-x 1 root root    84016 Jul 27  2018 /usr/bin/gpasswd
802327     36 -rwxr-sr-x 1 root tty     34896 Jan 10  2019 /usr/bin/wall
802144     72 -rwxr-sr-x 1 root shadow  71816 Jul 27  2018 /usr/bin/chage
802150     44 -rwsr-xr-x 1 root root    44528 Jul 27  2018 /usr/bin/chsh
802258     64 -rwsr-xr-x 1 root root    63736 Jul 27  2018 /usr/bin/passwd
802247     44 -rwsr-xr-x 1 root root    44440 Jul 27  2018 /usr/bin/newgrp
802190     32 -rwxr-sr-x 1 root shadow  31000 Jul 27  2018 /usr/bin/expiry
802147     56 -rwsr-xr-x 1 root root    54096 Jul 27  2018 /usr/bin/chfn
21684229  140 -rwsr-xr-x 1 root root   140944 Jan 31  2020 /usr/bin/sudo
810394    316 -rwxr-sr-x 1 root ssh    321672 Oct  6  2019 /usr/bin/ssh-agent
801624     36 -rwsr-xr-x 1 root root    34888 Jan 10  2019 /bin/umount
801609     64 -rwsr-xr-x 1 root root    65272 Aug  3  2018 /bin/ping
801618     64 -rwsr-xr-x 1 root root    63568 Jan 10  2019 /bin/su
801604     52 -rwsr-xr-x 1 root root    51280 Jan 10  2019 /bin/mount
```

进程权限约束：

```sh
python3 ssti_rce.py 'grep -E "NoNewPrivs|Cap(Inh|Prm|Eff|Bnd|Amb)" /proc/self/status'
```

```text
CapInh: 0000000000000000
CapPrm: 0000000000000000
CapEff: 0000000000000000
CapBnd: 00000000a80425fb
CapAmb: 0000000000000000
NoNewPrivs: 1
```

SUID 被抑制的实际证据：

```sh
python3 ssti_rce.py '/usr/bin/sudo -n -l 2>&1'
```

```text
sudo: effective uid is not 0, is /usr/bin/sudo on a file system with the 'nosuid' option set or an NFS file system without root privileges?
```

目标身份与文件权限：

```sh
python3 ssti_rce.py 'getent passwd dragon_lord; getent group dragon_lord; stat -c "%A %a %u %g %U %G %n" /flag.txt'
```

```text
dragon_lord:x:1001:1001::/home/dragon_lord:/bin/sh
dragon_lord:x:1001:
-rwx------ 700 1001 1001 dragon_lord dragon_lord /flag.txt
```

根文件系统挂载信息本身未显示 `nosuid`，进一步说明阻断来自进程级 `NoNewPrivs`：

```sh
python3 ssti_rce.py 'findmnt -no SOURCE,FSTYPE,OPTIONS /'
```

```text
overlay overlay rw,relatime,...,nouserxattr
```

### 结论
不可行。当前 SSTI 执行链继承了不可逆的 `NoNewPrivs: 1`，所有已发现 SUID/SGID 程序在 `execve` 时都不能提升到文件 owner/group；清单中也没有 uid/gid 1001 的定制二进制。因此不存在可用于读取 `/flag.txt` 或切换到 uid 1001 的 SUID/SGID 利用命令。

建议主线停止投入 SUID/SGID，转查不依赖 `execve` 权限增益的路径，例如已在其他分支进行的 file capabilities、root/dragon_lord 服务逻辑、可写特权配置或 IPC。若以后获得一个不带 `NoNewPrivs` 的执行入口，可用以下命令重新验证，而不是在当前 SSTI 上继续尝试：

```sh
/usr/bin/sudo -n -l
```
