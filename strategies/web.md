# Web 题攻击流程

## 侦察阶段 (5-10min)

1. curl 探测目标，识别技术栈 (Server header、X-Powered-By、Cookie 特征)
2. 端口扫描: `nmap -sV -p- <host>` (或快速 `nmap -sV <host>`)
3. 目录扫描: `ffuf -u <url>/FUZZ -w common.txt -mc 200,301,302,403`
4. 识别所有入口点 (GET/POST 参数、API 端点、上传点、登录框)
5. 发现 2+ 攻击向量时，调 `branch.py spawn` 并行试探

## 验证阶段 (每个向量 3-5min)

- 选最高优先级向量全力推进
- 单次失败不换方向
- 同一命令参数微调不超 3 次
- 同类操作连续 3 次无新发现 -> 换方向

## 利用阶段 (充分投入)

- 对确认漏洞深入利用
- 拿到 RCE 先读 flag: `cat /flag*`、`find / -name "flag*"`、`env | grep -i flag`
- 发现 flag 立即输出到 progress.md 的 Flags Found 段

## 常见攻击面清单

- SQL 注入: 联合查询 / 盲注 / 时间盲注 / 堆叠注入 / 二次注入
- XSS: 反射 / 存储 / DOM (CTF 中通常用于窃取 cookie 或打 bot)
- SSTI: {{7*7}} / ${7*7} / <%= 7*7 %> 测试不同模板引擎
- 文件上传: 绕过扩展名/MIME/内容检查，getshell
- 文件包含: LFI/RFI，读源码或日志getshell
- 命令注入: ; | || && ` $() 反引号绕过
- SSRF: 内网探测 / 读文件 / 打 Redis / gopher 协议
- 反序列化: PHP (__wakeup/__destruct) / Java / Python pickle
- 弱认证: 默认密码 / JWT 伪造 / session 固定
- 逻辑漏洞: 越权 / 条件竞争 / 参数篡改
