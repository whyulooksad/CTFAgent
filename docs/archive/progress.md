## Target
- URL: http://127.0.0.1:8080
- Background: 诊断 Dashboard 点击“启动”后任务立即停止
- Start Time: 2026-08-11

## Current Phase
model_catalog_fix_complete

## Next Steps
1. 当前已运行的旧 Codex 进程结束后，从 Dashboard 重新启动任务以加载更新后的 `ctf` profile
2. 在新生成的 `codex.log` 中确认不再出现 `codex_models_manager` 刷新超时

## Key Artifacts
- dashboard.py: Dashboard 后端
- challenges/manual_web_78ba8ead1559/: 截图对应任务目录
- branch.py: `_bind_socket()` 在长项目路径下报 `OSError: AF_UNIX path too long`
- run.sh: daemon 等待 3 秒后报 `Branch daemon failed to start` 并退出
- 当前 branch.sock 完整路径为 105 字节；失败点为 branch.py:122
- 当前代码实际使用 MD5 前 12 个十六进制字符，不是路径前 64 位
- `manual_web_78ba8ead1559` 文件夹名仅 23 字节，但完整 branch.sock 路径为 105 字节
- 历史提交 d056548 只修正 dashboard.py 的 hashlib 导入位置，没有 64 位截断
- run.sh、branch.py、dashboard.py 当前均与 HEAD 一致
- 当前 CODEX_HOME 未设置，CLI 默认使用 `/Users/shallowdream/.codex`
- 用户级 `hooks.json` 已删除；项目内尚无 `.codex/hooks.json`
- 官方支持项目级 Hook，且 run.sh 已带 `--dangerously-bypass-hook-trust`
- run.sh 已改为使用 `--profile ctf`
- README 已改为 CLI 专用 profile 配置说明
- `~/.codex/ctf.config.toml` 已安装（权限 0600），TOML 解析通过
- run.sh 通过 `bash -n`，Hook 路径可从 challenge 子目录正确解析
- `codex doctor` 不支持 `--profile`，需改用支持 profile 的 runtime/debug 命令验证
- `codex debug` 支持 profile 但不支持 `--strict-config`；该失败属于 CLI 参数限制
- `codex --profile ctf debug prompt-input` 已成功，Codex 原生加载 profile 通过
- `git diff --check`、`bash -n run.sh` 和 Hook no-op smoke test 均通过
- branch.py 已统一使用 `/tmp/ctf-agent-<uid>/branch-<workdir hash>.sock`
- run.sh 已通过 `branch.py socket-path` 获取并检测同一 socket
- dashboard.py 已移除旧 `$WORK_DIR/branch.sock` 前置检查
- 新 socket 路径为 51 字节，daemon/client 映射一致
- Python AST、`bash -n`、`git diff --check` 均通过
- 实际 daemon 绑定成功，status 返回空 subagent 列表，shutdown 正常并清理 socket
- `CTF_AGENT_SOCKET_DIR` 覆盖值限制为绝对路径，确保各进程寻址一致
- 最终 Python/Shell/whitespace 检查通过；旧 socket 引用仅保留兼容性清理
- 临时完整 run.sh 测试成功越过 daemon ready 并进入 Codex round 1/10
- 临时测试进程已全部终止，测试 socket 已清理，无残留进程
- `codex doctor --json` 显示 ChatGPT HTTP 后端请求超时，但 Responses WebSocket 握手成功
- 模型推理在报错后持续执行，因此 `codex_models_manager` 日志是后台模型目录刷新失败，不是 Agent 退出原因
- 官方配置支持 profile 级 `model_catalog_json`；`~/.codex/ctf.config.toml` 已指向共享的 `models_cache.json`
- branch.py 启动 Subagent 时也已增加 `--profile ctf`，主 Agent 与 Subagent 使用相同目录与 Hook 配置
- 最小只读 `codex exec --profile ctf` 成功返回 `PROFILE_OK`，未再出现模型目录刷新/子进程超时
- 修改前启动的 Codex 进程不会热加载 profile，旧 `codex.log` 仍可能继续出现原告警，需下一次启动生效

## Flags Found
(无)
