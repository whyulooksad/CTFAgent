# 二进制题攻击流程

## 侦察阶段 (5-10min)

1. 有附件: `file`/`checksec`(pwntools)/`strings`/`readelf -h` 识别架构、保护 (NX/PIE/RELRO/Canary)
2. 无附件: `nc`/`curl` 探测远程服务协议，抓取交互流程 (菜单/提示文本)
3. 判断题型: 栈溢出 / 堆利用 / 格式化字符串 / 整数溢出 / UAF / 逻辑漏洞 / 固件逆向 (f2 类 MCU 授权码)
4. 发现 2+ 可行方向时，调 `branch.py spawn` 并行试探

## 分析阶段

- GDB 调试本地复现 (`gdb ./binary`，远程服务对比行为)
- objdump/readelf 静态分析关键函数 (`objdump -d -M intel ./binary | less`)
- 逆向自定义协议: 记录字段结构、长度检查、边界条件
- 固件/MCU 类 (f2): 逆向授权码校验算法，寻找等价输入或比较绕过

## 利用阶段

1. 定位输入点与偏移 (`cyclic`/pattern)
2. pwntools 编写 exploit (`from pwn import *; remote(IP, PORT)`)
3. 无 shell 场景直接构造读 flag 的 ROP/格式化串链
4. 拿到 flag 立即输出到 progress.md 的 Flags Found 段

## 常见攻击面清单

- 栈溢出: 覆盖返回地址/EBP，ROP 绕 NX
- 格式化字符串: `%p` 泄露，`%n` 写
- 堆: double free / tcache poisoning / off-by-null
- 整数问题: 长度截断、负数索引、size 混淆
- 逻辑: 越界读写、未初始化、TOCTOU
- 沙箱逃逸 (e2 类): seccomp 规则审查，找允许的 syscall (openat/sendfile/orw)

## 约束

- 远程服务在 VPN 内网 (10.x)，直连不走代理
- 服务可能有连接频率限制，失败 payload 重试间隔 sleep
- 拿到交互报错先核对协议格式，不要盲发
