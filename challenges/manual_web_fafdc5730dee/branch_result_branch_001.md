## Branch Result
direction: route-source-enum
subagent_id: branch_001
status: FEASIBLE

### 发现
- 新静态资源路由：`/static/css/default.css`、`/static/css/materialize.min.css`、`/static/js/materialize.min.js`、`/static/js/jquery.min.js`、`/static/background.jpg`。
- `/static/css/default.css` 是本地小 CSS，内容包含 `background-image: url("../background.jpg");`，未发现 flag/source/config 关键词。
- `/static/css/materialize.min.css`、`/static/js/materialize.min.js`、`/static/js/jquery.min.js` 为标准前端库：Materialize v0.97.1、jQuery v1.11.3。jQuery 末尾有 `//# sourceMappingURL=jquery.min.map`，但 map 文件不存在。
- 常见隐藏路由、源码文件、配置文件、`.git`、备份包、静态路径穿越变体均未发现有效 source/config/flag 泄露；除已知 `/`、`/login`、`/register` 与上述静态资源外，测试路径基本为 `404 len=232` 基线。

### 命令和结果
- 模板链接/静态引用抽取：
```text
curl / | perl -ne 'print if /(?:href=|src=|action=|<!--|form|script|link)/i'
  <link rel="stylesheet" type="text/css" href="/static/css/default.css">
  <link rel="stylesheet" href="/static/css/materialize.min.css">
  <form class="login" action="/login" method="post" style="width:450px;">
  <a href="/register">Register now!</a>
  <script src="/static/js/materialize.min.js"></script>

curl /register | perl -ne 'print if /(?:href=|src=|action=|<!--|form|script|link)/i'
  <link rel="stylesheet" type="text/css" href="/static/css/default.css">
  <link rel="stylesheet" href="/static/css/materialize.min.css">
  <form class="login-form" action="/register" method="POST" style="width:400px;">
  Already have account? <a href="/login">Login</a>
  <script src="/static/js/materialize.min.js"></script>

curl -b /tmp/manual_web_auth_cookies.txt /
  <!--没错就是这么简洁~Red*s-->
```

- 新静态资源状态：
```text
/static/css/default.css          200 Content-Length: 1301   Content-Type: text/css
/static/css/materialize.min.css  200 Content-Length: 146430 Content-Type: text/css; charset=utf-8
/static/js/materialize.min.js    200 Content-Length: 122907 Content-Type: application/javascript; charset=utf-8
/static/js/jquery.min.js         200 Content-Length: 95992  Content-Type: application/javascript
/static/background.jpg           200 Content-Length: 908361 Content-Type: image/jpeg
```

- CSS/JS 进一步检查：
```text
grep urls from /static/css/default.css:
  ../background.jpg

grep urls from /static/css/materialize.min.css:
  ../font/material-design-icons/Material-Design-Icons.{eot,svg,ttf,woff,woff2}
  ../font/roboto/Roboto-{Bold,Light,Medium,Regular,Thin}.{ttf,woff,woff2}

source map probes:
  /static/css/default.css.map        404 232 text/html
  /static/css/materialize.min.css.map 404 232 text/html
  /static/js/materialize.min.js.map  404 232 text/html
  /static/js/jquery.min.map          404 232 text/html
  /static/js/jquery.min.js.map       404 232 text/html
```

- 隐藏路由/源码/配置枚举摘要：
```text
404 baseline: status=404 len=232 title='404 Not Found'

Targeted route/source/config sweep non-baseline results:
  NOAUTH 200 2771 /          (known login page)
  AUTH   200 540  /          (known Welcome page)
  NOAUTH 200 2771 /login     (known)
  AUTH   200 2771 /login     (known)
  NOAUTH 200 2067 /register  (known)
  AUTH   200 2067 /register  (known)

Checked examples included:
  /robots.txt, /sitemap.xml, /admin, /dashboard, /profile, /user,
  /flag, /flag.txt, /source, /src, /code, /backup, /download, /read,
  /debug, /console, /api/*, /app.py, /config.py, /requirements.txt,
  /Dockerfile, /.env, /.git/HEAD, /.git/config, /.git/index.
All were baseline 404 unless listed above.

Archive/backup probes:
  /{www,source,src,app,server,backup,code,web,project,flask,redis,ctf,dist,release}{.zip,.tar,.tar.gz,.tgz,.rar,.7z,.bak,.old,.orig,.save,.swp,~,.txt,.gz}
  source/config backup variants such as /app.py.bak, /config.py.save, /routes.py.old.
Result: no valid non-404 responses. A few curl 000 timeouts were retried sequentially and resolved to 404 len=232.

Static traversal/source probes:
  /static/../app.py, /static/%2e%2e/app.py, /static/..%2fapp.py,
  /static/../config.py, /static/../requirements.txt, /static/../flag,
  plus encoded/double-encoded variants.
Result: no valid disclosure; baseline 404.
```

### 结论
可行但低优先级：本分支发现了新的静态资源路由，便于主线补全资产图；未发现源码、配置、flag 或隐藏功能路由泄露。建议主线继续围绕登录后的 `Red*s` 注释、Flask-like signed UUID session、以及反序列化边界推进。
