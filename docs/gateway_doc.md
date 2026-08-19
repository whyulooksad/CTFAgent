# 大模型网关接入文档


## 1. 说明

大模型网关是透明代理。接入时通常只需要把代码中的大模型 `baseURL` 替换为控制台展示的“网关 URL”，模型名称、请求路径、请求体、`Authorization` 等上游鉴权 Header 仍按原大模型服务商要求传递。

凭证边界：

| 凭证 | 用途 | 是否用于大模型网关 |
| --- | --- | --- |
| 大模型 API Key | 调用 OpenAI、DeepSeek、Anthropic 等上游模型服务 | 是，由 Agent 自行持有并通过 `Authorization` 等 Header 传给上游 |
| 平台 AccessKey | 调用平台比赛能力接口，例如赛题列表、题目详情、提交 flag | 否，不用于大模型网关鉴权 |
| 网关 URL | 平台为某个原始 BaseURL 生成的代理访问地址 | 是，替换 Agent 代码中的模型 `baseURL` |

不要把大模型 API Key 填到平台 AccessKey 中，也不要把平台 AccessKey 当作模型 API Key 使用。

## 2. 接入前提

接入比赛网关前，需要满足以下条件：

1. 管理员已在“大模型服务商”中配置并启用允许访问的 API 端点。
2. 选手已进入 AI Agent 赛事控制台。
3. 选手已在“环境配置”页的“大模型 API 配置”中添加原始 URL。
4. 控制台已为该渠道生成可复制的网关 URL。
5. Agent 运行环境内保存了对应服务商的 API Key。

如果添加原始 URL 时提示“大模型 API 未在白名单内”，说明该地址没有命中平台允许的服务商端点，需要更换地址或联系赛事管理员确认白名单配置。


## 3. 配置步骤

### 3.1 添加大模型渠道

在选手控制台进入“环境配置”页，找到“大模型 API 配置”区域。

点击“添加渠道”，填写原始 URL。原始 URL 是模型 SDK 原本使用的 BaseURL，例如：

```text
https://api.deepseek.com
https://api.deepseek.com/anthropic
```

提交成功后，平台会展示该渠道的：

| 字段 | 说明 |
| --- | --- |
| 大模型厂商 | 命中的服务商名称 |
| 原始 URL | 选手填写的上游 BaseURL |
| 网关 URL | Agent 代码中应使用的新 BaseURL |
| 状态 | `启用` 或 `API 已失效` |

如果状态为 `API 已失效`，该渠道不可用。常见原因是管理员禁用了服务商、删除了服务商端点，或修改白名单后该渠道不再命中允许端点。

### 3.2 替换 Agent 代码中的 BaseURL

将原来的模型 BaseURL 替换为控制台中的网关 URL。网关 URL 形如：

```text
https://<platform-host>/llm-gateway/proxy/e/<endpointCode>
```

请直接复制控制台展示的完整网关 URL，不要自行拼接。

路径拼接规则：

| Agent 请求 | 实际转发到上游 |
| --- | --- |
| `{网关 URL}/v1/chat/completions` | `{原始 URL}/v1/chat/completions` |
| `{网关 URL}/v1/messages` | `{原始 URL}/v1/messages` |

Query 参数会原样透传给上游。

注意原始 URL 自带的路径会被保留。如果原始 URL 填写为 `https://api.example.com/v1`，则 Agent 请求 `{网关 URL}/chat/completions` 会转发到 `https://api.example.com/v1/chat/completions`；如果 Agent 请求 `{网关 URL}/v1/chat/completions`，则会转发到 `https://api.example.com/v1/v1/chat/completions`。建议根据所用 SDK 的路径拼接习惯选择原始 URL：

### 3.3 保留上游鉴权 Header

网关不会替你生成或保存大模型 API Key。Agent 仍需要按原服务商要求传递鉴权 Header，例如：

```http
Authorization: Bearer <MODEL_API_KEY>
Content-Type: application/json
```