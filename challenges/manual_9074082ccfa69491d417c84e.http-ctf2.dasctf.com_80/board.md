# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|--------|---------|
| 1 | pending | 等待目标实例恢复后进行技术栈识别 | - | 2026-07-31T01:49 |
| 2 | pending | 实例恢复后立即 ffuf 目录扫描 | - | 2026-07-31T01:49 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| 1 | fact | 目标在 DASCTF 新平台 (ctf2.dasctf.com)，BUUOJ 已归档迁移 | anysearch 搜索 | 2026-07-31T01:48 |
| 2 | evidence | 首页返回 502 "Target unavailable" (openresty)，DNS 解析至 117.21.200.176 (node5.buuoj.cn) | Codex curl | 2026-07-31T01:48 |
| 3 | fact | 502 通常表示容器实例未启动/已过期，需在平台 Web 界面手动启动 | anysearch 搜索 | 2026-07-31T01:48 |
| 4 | evidence | 绕过代理直连仍 502，排除本地代理问题 | Codex DNS+curl | 2026-07-31T01:48 |
| 5 | fact | 本地无源码/抓包/历史响应，仅有 agent 日志与状态文件 | Codex 目录检查 | 2026-07-31T01:48 |
| 6 | failure_boundary | HTTP / /robots.txt /favicon.ico / HTTPS / 直连IP+Host 均返回相同 502；IP 默认 vhost 返回 200 | Codex 多路径探测 | 2026-07-31T01:49 |
