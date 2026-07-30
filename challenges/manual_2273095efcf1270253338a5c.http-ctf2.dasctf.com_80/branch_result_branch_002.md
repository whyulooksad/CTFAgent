## Branch Result
direction: spring_surface
subagent_id: branch_002
status: INFEASIBLE

### 发现
- 首页 `GET /` 返回 `200`、`Content-Type: text/html; charset=utf-8`、`Server: openresty`，HTML 含 `xmlns:th="http://www.thymeleaf.org"`；但这只是静态命名空间标记，响应头和正文均未泄露 Spring Boot、Tomcat、Thymeleaf 或 Java 版本。
- 随机不存在路径返回 openresty 后的定制 HTML 404；直接访问 `/error` 即使指定 `Accept: application/json` 仍是同类 HTML 404，没有 Spring Whitelabel、异常堆栈、时间戳、应用路径或版本信息。
- 常见 Actuator 入口及敏感端点 `/actuator[/]`、`health`、`info`、`env`、`configprops`、`beans`、`mappings`、`heapdump`、`logfile` 全部为 `404`。
- H2、Swagger/OpenAPI、Webjars 和资源入口均未暴露：`/h2-console[/]`、`/swagger-ui.html`、`/swagger-ui[/index.html]`、`/v2/api-docs`、`/v3/api-docs`、`/api-docs`、`/swagger-resources[/configuration/ui]`、`/webjars/`、`/resources/`、`/META-INF/resources/` 全部为 `404`。
- `/static/js/jquery.min.js` 可正常读取（`200`），这是首页已引用的普通静态资产。可见前端版本为 jQuery 1.11.3、Bootstrap CSS 4.4.1、Bootstrap JS 3.3.5，但没有 Spring 组件版本泄露。
- 少量路径绕过 `/actuator%2fhealth`、`/actuator%252fhealth`、`/%2e/actuator`、`/;/actuator`、`/actuator;/health` 均为 `404`。
- 静态目录穿越变体 `/static/../application.properties`、`/static/%2e%2e/application.properties`、编码斜杠、矩阵参数和双重编码版本均为 `404`；根路径下 `application.properties`、`application.yml`、`bootstrap.yml` 也均为 `404`。
- `OPTIONS /actuator` 虽返回 `200`，但随机不存在路径和 `/` 的 `OPTIONS` 完全相同，均为 `Allow: OPTIONS, GET, HEAD` 和空响应体，因此这是全站自动 OPTIONS 行为，不代表 actuator 存在。
- `POST /error` 返回简短通用 `405 Method Not Allowed` HTML，其页面形态和自动 OPTIONS 行为并不提供 Spring 证据，反而说明仅凭首页的 `xmlns:th` 不能确认后端是 Spring。

### 命令和结果
基线：

```bash
curl -sS -D - http://2273095efcf1270253338a5c.http-ctf2.dasctf.com/
# HTTP/1.1 200 OK
# Server: openresty
# Content-Type: text/html; charset=utf-8
# Content-Length: 1709
# 正文含：<html lang="en" xmlns:th="http://www.thymeleaf.org">

curl -sS -D - http://2273095efcf1270253338a5c.http-ctf2.dasctf.com/__spring_probe_not_found_7c91
# HTTP/1.1 404 Not Found
# Server: openresty
# Content-Length: 563
# 定制 HTML 404，无 Spring 错误字段
```

核心路由探测（实际使用 `curl --path-as-is -sS --max-time 8`，状态/长度记录于 `/tmp/spring_routes.tsv`）：

```text
/actuator                 404
/actuator/health          404
/actuator/env             404
/actuator/mappings        404
/actuator/heapdump        404
/error                    404
/h2-console/              404
/swagger-ui.html          404
/swagger-ui/index.html    404
/v2/api-docs              404
/v3/api-docs              404
/swagger-resources        404
/webjars/                 404
/static/js/jquery.min.js  200  application/javascript  95992 bytes
```

内容协商与 OPTIONS 对照：

```bash
curl -sS -H 'Accept: application/json' -D - http://2273095efcf1270253338a5c.http-ctf2.dasctf.com/error
# 404 text/html; charset=utf-8，仍为定制 HTML 404

curl -sS -X OPTIONS -D - http://2273095efcf1270253338a5c.http-ctf2.dasctf.com/actuator
curl -sS -X OPTIONS -D - http://2273095efcf1270253338a5c.http-ctf2.dasctf.com/__random_options_8d31
# 两者均：200，Allow: OPTIONS, GET, HEAD，Content-Length: 0
```

路径绕过与配置文件：

```bash
curl --path-as-is -sS -D - http://2273095efcf1270253338a5c.http-ctf2.dasctf.com/actuator%2fhealth
curl --path-as-is -sS -D - http://2273095efcf1270253338a5c.http-ctf2.dasctf.com/actuator%252fhealth
curl --path-as-is -sS -D - http://2273095efcf1270253338a5c.http-ctf2.dasctf.com/actuator;/health
curl --path-as-is -sS -D - http://2273095efcf1270253338a5c.http-ctf2.dasctf.com/static/%2e%2e/application.properties
curl --path-as-is -sS -D - http://2273095efcf1270253338a5c.http-ctf2.dasctf.com/static/%252e%252e/application.properties
# 全部 404，响应为同类定制错误页
```

### 结论
INFEASIBLE。限定范围内没有发现可直接利用的 Spring Boot Actuator、`/error` 信息泄露、H2 Console、Swagger/OpenAPI、敏感配置静态暴露或常见路径绕过。首页的 Thymeleaf XML 命名空间不足以证明真实后端技术栈，现有 HTTP 行为也没有给出 Spring 版本或组件证据。建议主线转向登录逻辑、参数处理或应用自身业务端点，不再扩大 Spring 常见路由扫描。
