# Solver 同步记录 (main → master-agent)

每次把 main 分支的 solver 成品合并进本分支时在此追加一行。
镜像构建建议 tag: `ctf-solver:<日期>-<main短哈希>`，可精确回滚。

| 日期 | main commit | 同步范围 | 重放的 master 适配 | 备注 |
|------|-------------|----------|--------------------|------|
| 2026-08-16 | 8ec1e00 | run.sh/branch.py/monitor.py/dashboard.*/AGENTS.md/hermes_monitor.md/TOOLS.md (main 全部 8 提交) | ① --flag-count 多flag ② --type binary ③ Flags Found 噪音过滤 ④ grep -P→sed ⑤ REPO_ROOT 路径 ⑥ branch socket 短路径(AF_UNIX 108) ⑦ AGENTS.md Flags Found 约束(注: AGENTS.md 必须在仓库根,codex 从 work_dir 向上查找) ⑧ hermes skills 快照 | strategies/ 随 main 移除，binary 流程并入 TOOLS.md；镜像补 dirb/tshark/foremost + z3/pycryptodome/Pillow |
| 2026-08-18 | (本分支变更，非 main 合并) | **解题引擎替换: codex → claude code**。run.sh 重写 (llm.yaml→环境变量、claude -p stream-json、--managed STOP 模式、hermes_busy 收尾等待)；AGENTS.md→solver/AGENT.md (--append-system-prompt-file 注入，仓库根不再放)；branch.py 退役 (原生 Task 工具 + scout agent 替代)；codex.log→agent.log 全链路；master 新增 wrong-flag 清除+dead_ends 反馈+双死门 (_terminate)；面板「平台接入」改赛方大模型平台表单 (/api/connect-llm, api_key 掩码) | 镜像: @openai/codex→@anthropic-ai/claude-code@2.1.220 + IS_SANDBOX=1；快照: codex 部分删除，仅剩 hermes；llm.yaml 运行时挂载不进镜像 | 赛事环境只允许国产大模型，codex 接 deepseek 后工具调用崩溃 ("No tool output found for tool call")；claude code 经 ANTHROPIC_BASE_URL 接入 anthropic 兼容端点 (Cairn 验证过的模式) |
