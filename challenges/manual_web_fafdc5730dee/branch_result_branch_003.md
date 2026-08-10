## Branch Result
direction: http-edge-write-leaks
subagent_id: branch_003
status: INFEASIBLE
### 发现
- 基础页面行为：`/`、`/login` 均返回登录页并设置 `session=<uuid>.<sig>`；正常注册/登录后 cookie UUID 不变，`/` 反射 `Welcome,<username>`，HTML 注释仅提示 `Red*s`。
- 方法边界：`OPTIONS /login` 显示 `HEAD, GET, POST, OPTIONS`；`TRACE` 被 openresty 拦截为 405；`PUT` 到 Flask 405；`X-HTTP-Method-Override` 不生效。
- 表单/content-type：缺字段、空表单、JSON、text/plain、form body + JSON content-type 均为 400，无 debug/source 泄露；multipart、chunked、CL+TE、HTTP/1.0、Expect: 100-continue 均按普通表单处理并 302。重复字段能正常提交，但未形成新写入 primitive。
- Cookie/session 边界：空值、无签名、坏签名、多点号、quoted cookie、逗号、路径穿越样式、长 cookie 都 fail closed，服务端发新 signed UUID，无 stack trace。重复 `session` cookie 时 Werkzeug 取后一个值；有效值在后则认证有效，有效值在前/无效值在后则退回新匿名 session。这只是解析规则，未给出签名绕过或 Redis 写入。
- 反射/XSS：用户名中的 `<img ...>"&'` 在 welcome 页被转义为 `&lt;...&gt;&#34;&amp;&#39;`，无 cookie 注入或 XSS 边路。
- vhost/proxy/cache：`Host: 127.0.0.1` 仅返回平台/openresty 空 502；`X-Forwarded-Host/Proto/For`、`X-Original-URL`、`X-Rewrite-URL` 未改变 Flask 路由或 redirect host。动态页 `Cache-Control: no-cache`；静态查询响应不被 openresty 共享缓存，重复请求均 `X-Cache: MISS` 且 Set-Cookie 不复用。
- 静态/source 泄露：`/static/../app.py`、encoded traversal、off-by-slash alias、path params、null byte、带 query 的 traversal 均 404 或 openresty 400。`/static/css/default.css` 无 query 时由 openresty 缓存返回 1301-byte 2019 CSS；带 query 时走 Flask static，返回另一个 5690-byte CSS 并 Set-Cookie，但内容无 `secret/Redis/pickle/flag/app =/Flask/password` 等敏感关键词，未形成读取应用源文件能力。
- Werkzeug debugger probes `?__debugger__=yes&cmd=resource&f=...` 返回普通页面，无 debugger/resource 泄露。
### 命令和结果
- `curl -i /`, `/login`, `/register`: 200，登录/注册页；`Set-Cookie: session=<uuid>.<sig>`；登录后 `/` 显示 `Welcome,<username>` 和注释 `<!--没错就是这么简洁~Red*s-->`。
- `curl -i -X OPTIONS /login`: `200 OK`, `Allow: HEAD, GET, POST, OPTIONS`。`TRACE /login`: openresty `405 Not Allowed`。`PUT /login`: Flask `405 Method Not Allowed`。
- Python requests content-type sweep against `/register` and `/login`: missing/empty/JSON/text/plain/form-json mismatch -> 400; multipart/duplicate fields/charset/bad percent -> 302 normal flow; no traceback/source body.
- Cookie sweep:
  - `Cookie: session=abc`, `abc.def`, `a.b.c.d`, `1111....`, `!!!!`, quoted, comma, long -> 200 login page with newly issued `session=<uuid>.<sig>`.
  - Duplicate order: `session=invalid; session=<valid>` kept authenticated session; `session=<valid>; session=invalid` produced fresh anonymous session.
- Proxy/header probes:
  - `Host: 127.0.0.1 /register` -> `502 Bad Gateway` empty body from platform proxy.
  - `X-Forwarded-Host: attacker.test` on POST `/register` -> 302 `Location: http://9bc98ac1fbf88cb6911b31bb.http-ctf2.dasctf.com/`.
  - `X-Original-URL`/`X-Rewrite-URL` ignored.
- Static/path probes:
  - `/static/css/default.css` -> 200 len 1301, `X-Cache: HIT`, no Set-Cookie.
  - `/static/css/default.css?x=1` -> 200 len 5690, `X-Cache: MISS`, Set-Cookie; no sensitive keywords.
  - `/static../app.py`, `/static..%2fapp.py`, `/static/%2e%2e/app.py`, `/static/css/%2e%2e/%2e%2e/app.py?x=1` -> 404.
  - `/static/css/default.css%00.py` -> openresty 400.
- Cache repeat test on `/static/css/materialize.min.css?branch_cache_repeat=<rand>` three times: all `X-Cache: MISS`, each with different Set-Cookie, so no shared cached session fixation.
### 结论
当前 HTTP edge-case 分支未发现可行 primitive。没有可用的 HTTP-side 任意 Redis 写、session id 签名绕过、session/source/secret 泄露、debugger 泄露或缓存共享 Set-Cookie。建议主线不要在这些具体边界上重复投入；后续只保留“发现新的真实 HTTP sink/功能点”或“从题目/平台外部获得真实 Redis/session/secret 连接信息”两类方向。
