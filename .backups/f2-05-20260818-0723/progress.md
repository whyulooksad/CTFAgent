## Target
- Type: binary
- URL: http://10.0.186.242:9105
- Attachment: /download (same validator, sha256 78233ae81c50524d58e087cfcfd0232d1c9a7ffd533bdf417df5f2c895db5235)
- Background: 嵌入式授权引擎；VM_KEY07 通过主校验，VM 输出 31 个 '.'
- Start Time: 2026-08-17T22:52:26+00:00

## Current Phase
exploit

## Next Steps
1. 深挖 blob@0x4020 解密算法（已知前缀 flag{ 反推密钥 51584aaa92...）
2. 分析 .data 全部字节、0x4008 自指针、跳转表作为可能的密钥素材
3. 测试更多流密码/PRNG/复合变换，避免重复上轮已试
4. 远程仅 GET，/download 与本地同 sha256；POST 全部 501，无需继续爆破 Web

## Key Artifacts
- /tmp/dis.asm: validator 完整反汇编
- /tmp/root.html: 服务首页
- /tmp/validator_remote: 远程下载同 sha256
- /tmp/resp_*: 路由探测响应

## Flags Found
(无)
