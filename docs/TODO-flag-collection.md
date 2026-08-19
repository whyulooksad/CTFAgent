# 待办 / 设计方案（未实施，用户待定）

## 2026-08-19: flag 全量收集 + 过滤提交（用户暂不采纳，考虑中）

### 背景
不能完全信任 agent 听话（progress.md 的 Flags Found 段可能不写/写错/漏写）。
昨天（2026-08-18）讨论过的方案，今天用户重新提起觉得可行，但暂不改。

### 方案
1. **收集**: master 从 work_dir 的所有 md / log 正则提取 flag{...} 候选
   - progress.md / board.md（hermes 可能记录了 flag）
   - codex.log / codex_round*.log（agent 输出里出现过的）
   - 各种产物文件
2. **过滤**:
   - 已提交过的 flag（submit_results.jsonl / flags_seen）→ 排除
   - 被判 wrong 的 flag（submit_results.jsonl status=wrong）→ 排除
3. **提交**: 过滤后的未知候选逐个尝试提交

### 当前实现差距
- master._read_flags 只读 progress.md 的 Flags Found 段
- mark_flag_seen 防重复提交 ✓（已提交的不重复）
- wrong 过滤只靠 run.sh 纠错（清 progress.md + dead_ends），master 侧无主动排除
- board.md / 日志里的 flag 完全没收集

### 参考
- VulnClaw `_extract_flags` / `_unverified_flags`（evidence 验证）
- monitor.py `check_flag_found`（codex.log 增量 flag 检测，仅用于 Hermes 信号）
