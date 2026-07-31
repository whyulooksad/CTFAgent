## Branch Result
direction: source-leak-enum
subagent_id: branch_001
status: INFEASIBLE

### 发现
- 未发现可帮助利用的 PHP 源码/备份泄露。
- 目标对大量不存在的非 PHP 路径返回首页内容：`200 OK`, body length `3342`, SHA256 prefix `9f210cdf65bd`。因此 `/index.php.bak`、`/check.php.bak`、`/www.zip`、`/.git/HEAD` 等如果返回同一首页，均判定为 rewrite/假阳性。
- 已覆盖 `index.php` / `check.php` 的常见源码与备份名：`~`, `.bak`, `.backup`, `.old`, `.orig`, `.save`, `.sav`, `.swp`, `.swo`, `.tmp`, `.txt`, `.inc`, `.src`, `.phps`, `.phtml`, `._bak`, `_bak`, `-old`, `.bak.php`, `.old.php`, 隐藏 vim swap 形式 `/.index.php.swp`、`/.check.php.swp` 等。
- 已覆盖常见敏感路径：`.git`, `.svn`, `.hg`, `.env`, `.user.ini`, `.htaccess`, `composer.json`, `Dockerfile`, `docker-compose.yml`, `README*`, `phpinfo.php`, `config.php`, `db.php`, `conn.php`, `connect.php`, `database.php`, `flag*`, 常见源码/备份归档如 `www.zip`, `source.zip`, `backup.tar.gz`, 以及 `backup/`, `src/`, `include/`, `vendor/`, `data/`, `logs/`, `image/` 等目录。
- 少量真实差异只说明资源不存在或禁止列目录：`/phpinfo.php`, `/config.php`, `/db.php`, `/flag.php` 等 PHP 文件返回通用 nginx `404 Not Found`；`/image/` 返回通用 nginx `403 Forbidden`；这些内容不泄露源码、配置或 flag。

### 命令和结果
- 基线：
  - `curl -i -sS --max-time 8 'http://6fb2ba677ad56a139506d872.http-ctf2.dasctf.com/'`
  - 结果：`200 OK`, `Content-Type: text/html; charset=UTF-8`, `X-Powered-By: PHP/7.3.11`, 首页登录表单。
- 不存在路径基线：
  - `curl -i -sS --max-time 8 'http://6fb2ba677ad56a139506d872.http-ctf2.dasctf.com/__definitely_missing_001__.txt'`
  - 结果：`200 OK`, body 与首页相同，说明非 PHP 未命中路径会 rewrite 到首页。
- 枚举脚本：
  - 使用 Python/urllib 对 397 个候选路径 GET 请求并按 body hash 与首页/缺失页比较。
  - 结果：`checked=397 deviating=23`，差异均为通用 `404 Not Found`、`403 Forbidden` 或重试后回到首页；无源码文本、备份文件、归档文件、git 元数据、环境变量或配置内容。
- 复核重点 URL：
  - `http://6fb2ba677ad56a139506d872.http-ctf2.dasctf.com/index.php.bak` -> `200 OK`，body 为首页，不是备份源码。
  - `http://6fb2ba677ad56a139506d872.http-ctf2.dasctf.com/check.php.bak` -> `200 OK`，body 为首页，不是备份源码。
  - `http://6fb2ba677ad56a139506d872.http-ctf2.dasctf.com/includes.zip` -> `200 OK`，body 为首页，不是 zip。
  - `http://6fb2ba677ad56a139506d872.http-ctf2.dasctf.com/image/` -> `403 Forbidden`, generic nginx page only.

### 结论
不可行。当前 source/backup leak 枚举未发现能帮助利用的可访问 URL 或内容；建议主线继续推进已确认的 `check.php` SQL 注入过滤绕过方向。
