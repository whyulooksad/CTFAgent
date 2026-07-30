# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|--------|---------|
| I1 | failed | 源码泄露获取 SECRET_KEY | 27条路径全404（app.py, .git, backup, source等） | 2026-07-31T02:21 |
| I2 | testing | 注册回显点 SSTI | 登录页 SSTI 无效，注册后首页 Welcome 回显待测 | 2026-07-31T02:21 |
| I3 | testing | **Redis pickle 反序列化 RCE（主攻方向）** | 确认 SWPU 2019 原题，Redis 暴露在额外端口，session 存为 pickle，key=session:{UUID} | 2026-07-31T02:22 |
| I4 | pending | 弱 SECRET_KEY 爆破 | 10个手动候选不匹配，但此攻击链不需要 SECRET_KEY | 2026-07-31T02:22 |
| I5 | pending | Redis 端口直接连接 | 待执行：Python socket 扫 6379/16379/26379 等端口 | 2026-07-31T02:22 |
| I6 | failed | SQL 注入 login | `' or 1=1--` 和 `'` 均返回 "incorrect"，排除 | 2026-07-31T02:21 |
| I7 | pending | SSRF 到 Redis | 如果 Redis 不直接暴露，寻找 SSRF 向量 | 2026-07-31T02:22 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| M1 | fact | 目标 Flask 登录页，标题 "Deserialization Login"，POST /login + /register，openresty 代理 | codex.log curl | 2026-07-31T02:18 |
| M2 | fact | session cookie = UUID.base64url_signature，itsdangerous Signer HMAC-SHA1 签名（27字符），每次请求签发新 UUID | codex.log | 2026-07-31T02:20 |
| M3 | hint | HTML 注释 "没错就是这么简洁~Red*s" = Redis，暗示 session 后端为 Redis + pickle | 首页 HTML | 2026-07-31T02:21 |
| M4 | fact | 27条高价值路径全 404：admin/upload/download/console/config/app.py/.git/backup 等 | /tmp/manual_web_probe2.txt | 2026-07-31T02:21 |
| M5 | fact | 注册 cx73194/pw73194 成功，登录后首页回显 Welcome,cx73194，cookie UUID=3acb0294-63e8-4bc3-b29d-ea83b24ca836 | codex.log | 2026-07-31T02:20 |
| M6 | failure_boundary | 登录页无 SQLi（`' or 1=1--` 无效）、无 SSTI（`{{7*7}}` 无效）、username[]=x 触发 400 Werkzeug 错误 | codex.log | 2026-07-31T02:21 |
| M7 | external | **SWPU 2019 原题确认**：WP 搜索命中 nikoeurus.github.io + CSDN bmth666，原题标题"Deserialization"，注册后登录只有提示，给额外端口=Redis，session 以 pickle 存 Redis，key=session:{UUID} | anysearch WP 搜索 | 2026-07-31T02:22 |
| M8 | external | **攻击链不需要 SECRET_KEY**：已有 cookie 签名有效，只需覆盖 Redis 中 session:{UUID} 的值为恶意 pickle，访问页面即触发 pickle.loads -> RCE | THUCTF2019 + Samsung CTF 2018 WP | 2026-07-31T02:22 |
| M9 | fact | 系统无 redis-cli 和 nmap，需用 Python socket 连接 Redis | terminal which 检查 | 2026-07-31T02:22 |
| M10 | external | Flask-Session < 0.7.0 默认用 pickle 序列化，>= 0.7.0 用 msgspec。题目名"Deserialization"暗示 pickle 版本 | Flask-Session 文档 | 2026-07-31T02:22 |
| M11 | fact | 参考脚本已写入 reference_redis_pickle_attack.py，包含端口扫描/密码爆破/pickle注入/触发全流程 | guidance.md | 2026-07-31T02:22 |
