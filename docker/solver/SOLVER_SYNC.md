# Solver 同步记录 (main → master-agent)

每次把 main 分支的 solver 成品合并进本分支时在此追加一行。
镜像构建建议 tag: `ctf-solver:<日期>-<main短哈希>`，可精确回滚。

| 日期 | main commit | 同步范围 | 重放的 master 适配 | 备注 |
|------|-------------|----------|--------------------|------|
| 2026-08-16 | 8ec1e00 | run.sh/branch.py/monitor.py/dashboard.*/AGENTS.md/hermes_monitor.md/TOOLS.md (main 全部 8 提交) | ① --flag-count 多flag ② --type binary ③ Flags Found 噪音过滤 ④ grep -P→sed ⑤ REPO_ROOT 路径 ⑥ branch socket 短路径(AF_UNIX 108) ⑦ AGENTS.md Flags Found 约束(注: AGENTS.md 必须在仓库根,codex 从 work_dir 向上查找) ⑧ hermes skills 快照 | strategies/ 随 main 移除，binary 流程并入 TOOLS.md；镜像补 dirb/tshark/foremost + z3/pycryptodome/Pillow |
