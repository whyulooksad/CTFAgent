# CTF 环境工具手册

环境已预装以下工具。**做题前先想清楚用什么工具，需要时 cat 本文件查看用法。**
不要自己写 curl 并发脚本做目录扫描——用 ffuf。

## Web 题

### nmap — 端口/服务扫描
```bash
nmap -sV -sC target.com          # 版本+默认脚本
nmap -p- target.com              # 全端口
nmap -p- --min-rate 2000 target  # 快速全端口
```

### ffuf — 目录/参数 fuzz（主力，别写 curl 脚本）
```bash
ffuf -u http://target/FUZZ -w /usr/share/wordlists/dirb/common.txt -mc 200,301,302,401,403
ffuf -u http://target/api/v1/FUZZ -w /usr/share/wordlists/dirb/common.txt -mc all -fc 404
ffuf -u "http://target/page?file=FUZZ" -w /usr/share/seclists/Discovery/Web-Content/raft-small-files.txt
ffuf -u http://target/FUZZ -w dict.txt -H "X-Forwarded-For: 127.0.0.1"  # 加头
# 常用字典位置：
#   /usr/share/wordlists/dirb/common.txt (small)
#   /usr/share/wordlists/dirb/big.txt     (medium)
#   /usr/share/seclists/Discovery/Web-Content/raft-small-files.txt (files, 若已装 seclists)
```

### jq — JSON 响应处理
```bash
curl -s http://target/api | jq .                    # 格式化
curl -s http://target/api | jq '.data[] | .name'    # 提取字段
curl -s http://target/api | jq 'keys'               # 看字段名
```

### curl — HTTP 请求（基础）
```bash
curl -s -k http://target/                          # -k 忽略证书
curl -s -X POST http://target/login -d 'user=admin&pass=1' -H 'Content-Type: application/x-www-form-urlencoded'
curl -s -b "session=xxx" http://target/admin       # 带 cookie
curl -s -i http://target/                          # 看响应头
curl -s http://target/ -H "X-Forwarded-For: 127.0.0.1"
```

## Misc 题（附件/图片/流量）

### file / strings / xxd — 文件识别
```bash
file mystery.bin                   # 真实文件类型
strings -n 8 mystery.bin           # 提取可打印字符串（-n 8 过滤短串）
xxd mystery.bin | head             # 十六进制查看头部
xxd -r -p hex.txt > out.bin        # hex 转回二进制
```

### exiftool — 图片元数据
```bash
exiftool image.jpg                 # 全部元数据（注意 Comment/Copyright/Artist 字段藏 flag）
```

### steghide — JPEG/BMP/WAV 隐写
```bash
steghide extract -sf image.jpg                       # 无密码提取
steghide extract -sf image.jpg -p "pass"             # 带密码
steghide info image.jpg                              # 查看是否嵌了东西
```

### binwalk — 固件/文件提取
```bash
binwalk mystery.png                 # 看嵌入的文件
binwalk -e mystery.png              # 自动提取到 _mystery.png.extracted/
```

### foremost — 文件雕刻（删除的文件/隐藏文件）
```bash
foremost -i mystery.bin -o outdir   # 按文件头雕刻出所有文件
```

### tshark — pcap 流量分析
```bash
tshark -r capture.pcap -Y "http"                    # 过滤 HTTP 流量
tshark -r capture.pcap -Y "http.request" -T fields -e http.host -e http.request.uri
tshark -r capture.pcap -Y "tcp.port==80" -T fields -e data.data | xxd -r -p   # 还原数据
tshark -r capture.pcap -z follow,tcp,ascii,0         # 跟踪 TCP 流 (0=第0条流)
```

## Crypto 题

### openssl — 加解密瑞士军刀
```bash
openssl enc -d -aes-256-cbc -in flag.enc -out flag.txt -k "password"   # 解密
openssl rsa -in key.pem -text -noout                 # 查看 RSA 私钥参数
openssl rsautl -decrypt -in enc.bin -inkey key.pem   # RSA 解密
openssl base64 -d -in enc.b64 -out raw.bin           # base64 解码
openssl dgst -md5 flag.txt                           # 计算哈希
```

### z3-solver (python) — 约束求解
```python
from z3 import *
s = Solver()
x, y = Ints('x y')
s.add(x*x + y*y == 100, x + y == 14)
if s.check() == sat:
    print(s.model())
```

### pycryptodome (python) — 密码原语
```python
from Crypto.Util.number import long_to_bytes, bytes_to_long, inverse, GCD
from Crypto.Cipher import AES
# 常见用途: RSA 参数运算(n/e/d/p/q)、AES/RC4 解密、padding oracle
```

### pwntools (python) — 网络交互/编码/交互式解题
```python
from pwn import *
# 远程交互
r = remote('target.com', 1337)
r.sendline(b'payload')
r.recvuntil(b'flag:')
print(r.recvline())
# 编码转换
print(xor(b'data', 0x41))        # 异或
print(base64.b64encode(b'x'))
```

## 通用

### python3 — 一切皆可 Python
- requests 库发 HTTP 请求（Cookie 保持、会话管理）
- Pillow 处理图片像素（LSB 隐写等）
- 网络 socket 写交互脚本

## 字典位置速查
```bash
/usr/share/wordlists/dirb/common.txt   # 目录扫描小字典
/usr/share/wordlists/dirb/big.txt      # 目录扫描中字典
# 如需 seclists: sudo apt-get install -y seclists
```

## 缺工具怎么办
- 工具不在列表里：`sudo apt-get install -y <工具>` 或 `python3 -m pip install --user --break-system-packages <库>`
- 装不了就换思路，别卡在装工具上

## 二进制题（远程服务 / 制品逆向）

### checksec — 保护机制速览 (pwntools)
```bash
python3 -c "from pwn import *; print(ELF('./pwn').checksec())"   # NX/PIE/Canary/RELRO
```

### objdump / readelf — 静态分析
```bash
objdump -d -M intel ./pwn | less          # 反汇编 (Intel 语法)
objdump -d -M intel ./pwn | grep -A20 '<main>:'   # 只看某函数
readelf -h ./pwn                          # ELF 头 (架构/入口)
strings -n 8 ./pwn | grep -i flag         # 找 flag 相关字符串
```

### gdb — 动态调试（本地复现）
```bash
gdb ./pwn
  (gdb) break main        # 断点
  (gdb) run               # 运行
  (gdb) x/20xg $rsp       # 看栈
  (gdb) info registers
```

### pwntools — exploit 编写（主力）
```python
from pwn import *
context.arch = 'amd64'
r = remote('10.x.x.x', 9999)      # 远程服务 (VPN 内网，直连)
r.sendline(b'A' * 72 + p64(0xdeadbeef))   # 栈溢出覆写返回地址
print(r.recvall(timeout=3))
```

### 二进制题流程
1. 有制品: checksec → strings → objdump 定位输入点与漏洞函数
2. 无制品: `nc` 探测远程服务协议，记录交互流程（菜单/提示/长度限制）
3. 定位偏移 (`cyclic`) → 构造 exploit → 无 shell 场景直接 ROP/格式化串读 flag
4. 常见漏洞面: 栈溢出 / 格式化字符串 (`%p` 泄露 `%n` 写) / 堆 (tcache/double free) /
   整数截断 / 逻辑越界；固件类 (f2) 逆向授权码校验算法找等价输入
5. 服务可能有连接频率限制，payload 失败后 sleep 再试，别高频重连
