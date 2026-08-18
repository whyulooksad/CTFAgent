#!/usr/bin/env python3
"""
llm_config.py -- 大模型引擎接入配置 (llm.yaml) 的读写与环境注入。纯文件配置，面板不写入。

解题引擎 claude code 通过环境变量接入国产大模型 (Cairn 验证过的模式):
  ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL

llm.yaml 是扁平 key: value 结构 (标准库解析即可，不引入 pyyaml 依赖):
  platform  赛方平台名称 (仅展示)
  base_url  Anthropic Messages API 兼容端点
  api_key   平台密钥 (面板展示时必须走 mask_key 掩码)
  model     模型名
  effort    可选思考档位 (空 = 不传 --effort)

消费方:
  master 启动时        load + apply_env (prioritizer 的 claude -p 子进程继承)
  solver run.sh         自带同款扁平解析 (bash 内联 python，见 run.sh)
  DockerBackend         把 llm.yaml 挂载进容器 /opt/ctf-agent/llm.yaml:ro
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
LLM_YAML = REPO_DIR / "llm.yaml"

# 字段顺序即写入顺序 (hermes_* 为监督引擎接入，留空沿用主配置/自带默认)
_FIELDS = ("platform", "base_url", "api_key", "model", "effort",
           "hermes_provider", "hermes_base_url", "hermes_api_key", "hermes_model")


def load(path: Path = LLM_YAML) -> dict:
    """读 llm.yaml，缺文件/坏行回退空值。"""
    cfg = {k: "" for k in _FIELDS}
    if not path.exists():
        return cfg
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in cfg:
            cfg[k] = v
    return cfg


def save(cfg: dict, path: Path = LLM_YAML) -> dict:
    """校验 + 写 llm.yaml (保留未识别字段不丢——只重写已知字段段落)。"""
    base_url = str(cfg.get("base_url", "")).strip()
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ValueError("base_url 必须以 http:// 或 https:// 开头")
    out = {k: str(cfg.get(k, "")).strip() for k in _FIELDS}
    header = (
        "# 大模型引擎接入配置 (手动编辑; docker 运行时挂载，换模型不重建镜像)\n"
        "#\n"
        "# 解题引擎是 claude code CLI，通过环境变量接入国产大模型 (Cairn 验证过的模式):\n"
        "#   base_url -> ANTHROPIC_BASE_URL   必须是 Anthropic Messages API 兼容端点\n"
        "#   api_key  -> ANTHROPIC_AUTH_TOKEN\n"
        "#   model    -> ANTHROPIC_MODEL (同时设为 ANTHROPIC_SMALL_FAST_MODEL，避免后台小模型调用打到不可用端点)\n"
        "#\n"
        "# 常见平台端点 (以赛方实际提供为准):\n"
        "#   DeepSeek: https://api.deepseek.com/anthropic  (model: deepseek-chat / deepseek-reasoner)\n"
        "#\n"
        "# 留空 api_key 时按本机 claude 默认登录态运行 (本地开发调试用)。\n"
        "\n"
        f'platform: "{out["platform"]}"\n'
        f'base_url: "{out["base_url"]}"\n'
        f'api_key: "{out["api_key"]}"\n'
        f'model: "{out["model"]}"\n'
        "\n"
        "# 可选: 思考档位 low | medium | high | xhigh | max (端点支持 thinking 时用，留空不传)\n"
        f'effort: "{out["effort"]}"\n'
        "\n"
        "# ── Hermes 监督引擎接入 (留空 = 沿用 ~/.hermes 自己的 provider/凭据池) ──\n"
        "# hermes 走 OpenAI 兼容协议，端点与上方 claude 的 /anthropic 不同 (DeepSeek: https://api.deepseek.com)\n"
        "# hermes_api_key / hermes_model 留空时沿用上方主配置的 api_key / model\n"
        "# hermes_provider 是 hermes 内置 provider 名 (deepseek/glm/openai/minimax/...)，\n"
        "# 对应环境变量 <名称大写>_API_KEY / <名称大写>_BASE_URL\n"
        f'hermes_provider: "{out["hermes_provider"]}"\n'
        f'hermes_base_url: "{out["hermes_base_url"]}"\n'
        f'hermes_api_key: "{out["hermes_api_key"]}"\n'
        f'hermes_model: "{out["hermes_model"]}"\n'
    )
    path.write_text(header, encoding="utf-8")
    return out


def apply_env(cfg: dict) -> None:
    """llm.yaml -> claude code 环境变量 (已有同名环境变量时不覆盖，显式 env 优先)。"""
    env_map = {
        "base_url": ("ANTHROPIC_BASE_URL", "ANTHROPIC_BASE_URL"),
        "api_key": ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"),
        "model": ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL"),
    }
    for key, (env, small) in env_map.items():
        val = os.environ.get(env, "").strip() or str(cfg.get(key, "")).strip()
        if val:
            os.environ[env] = val
            if key == "model":  # 后台小模型调用走同一端点
                if not os.environ.get("ANTHROPIC_SMALL_FAST_MODEL"):
                    os.environ["ANTHROPIC_SMALL_FAST_MODEL"] = val
    os.environ.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")


def mask_key(key: str) -> str:
    """api_key 展示掩码: sk-abcdefgh1234 -> sk-***1234；空串原样。"""
    key = (key or "").strip()
    if not key:
        return ""
    return f"{key[:3]}***{key[-4:]}" if len(key) > 8 else "***"


def status(cfg: dict | None = None) -> dict:
    """面板/日志用的只读状态 (绝不回传完整 key)。"""
    cfg = cfg if cfg is not None else load()
    return {
        "platform": cfg.get("platform", ""),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "effort": cfg.get("effort", ""),
        "api_key": mask_key(cfg.get("api_key", "")),
        "configured": bool(cfg.get("base_url") and cfg.get("api_key")),
        # hermes 监督引擎 (configured = llm.yaml 显式接管)
        "hermes_provider": cfg.get("hermes_provider", ""),
        "hermes_model": cfg.get("hermes_model", "") or cfg.get("model", ""),
        "hermes_api_key": mask_key(
            cfg.get("hermes_api_key", "") or cfg.get("api_key", "")
        ),
        "hermes_configured": bool(cfg.get("hermes_provider") and cfg.get("hermes_base_url")),
    }
