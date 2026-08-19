#!/usr/bin/env python3
"""
cred_snapshot.py -- 容器凭据"精制快照"生成器 (master-agent-spec.md §7.1/§7.2)。

不是 cp -r：选择性拷贝 + 路径重写 + 生成最小配置。
路径重写规则 host 无关: 宿主机 home 前缀 → /root (部署宿主是 WSL，开发机可能是 macOS，
不能硬编码具体用户路径)。

输出: cred_snapshots/<run_id>/
  codex/
    auth.json          原样
    models_cache.json  原样 (容器内避免重复刷新模型目录)
    hooks.json         重写: hook 命令 → /opt/ctf-agent/solver/hooks/check_guidance.py 绝对路径
    config.toml        生成的最小配置 (剔除宿主机 config.toml 里的桌面版专属段:
                       notify / marketplaces / mcp_servers / desktop / projects)
  hermes/
    auth.json          原样
    config.yaml        home 前缀重写保险 (实测本机无绝对路径)
    .env               home 前缀重写保险
    skills/            用户 skills 整体复制 (如 ctf-supervisor-knowledge)

用法:
  python3 cred_snapshot.py            # 生成快照并打印路径
Master 集成: backend=docker 时 make_backend 自动调用 ensure_snapshot()。
"""

from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent          # master/
REPO_DIR = SCRIPT_DIR.parent                          # 仓库根
SNAPSHOT_ROOT = REPO_DIR / "cred_snapshots"
HOME = Path.home()

DEFAULT_MODEL = "gpt-5.5"


def _warn(msg: str) -> None:
    print(f"[snapshot] 警告: {msg}", file=sys.stderr)


def _rewrite_home_paths(text: str) -> str:
    """宿主机 home 前缀 → /root (host 无关)。"""
    return text.replace(str(HOME), "/root")


# ─── Codex (§7.1) ───


def _read_model(codex_home: Path) -> str:
    """从宿主 config.toml 读 model，读不到用默认。"""
    config = codex_home / "config.toml"
    if config.exists():
        try:
            import tomllib
            data = tomllib.loads(config.read_text(encoding="utf-8"))
            model = data.get("model")
            if isinstance(model, str) and model:
                return model
        except Exception:
            pass
        m = re.search(r'^model\s*=\s*"([^"]+)"', config.read_text(encoding="utf-8"), re.MULTILINE)
        if m:
            return m.group(1)
    return DEFAULT_MODEL


def _extra_provider_sections(src: Path) -> str:
    """把宿主的 model_provider / model_providers 透传到容器配置。

    宿主机可能配置了自定义模型提供方 (如 deepseek/verytoken)，容器 codex
    没有这些段会回退默认 provider 导致请求发错地方。
    """
    config = src / "config.toml"
    if not config.exists():
        return ""
    try:
        import tomllib

        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except Exception:
        return ""

    lines: list[str] = []
    mp = data.get("model_provider")
    if isinstance(mp, str) and mp:
        lines.append(f'model_provider = "{mp}"')
    providers = data.get("model_providers")
    if isinstance(providers, dict):
        for name, p in providers.items():
            if not isinstance(p, dict):
                continue
            lines.append(f"\n[model_providers.{name}]")
            for k, v in p.items():
                if isinstance(v, str):
                    lines.append(f'{k} = "{v}"')
                elif isinstance(v, bool):
                    lines.append(f"{k} = {'true' if v else 'false'}")
                elif isinstance(v, (int, float)):
                    lines.append(f"{k} = {v}")
    # features 段透传 (如 multi_agent_v2 关闭: 容器 codex 走 V1 通信)
    features = data.get("features")
    if isinstance(features, dict):
        for fname, fval in features.items():
            if isinstance(fval, dict):
                lines.append(f"\n[features.{fname}]")
                for k, v in fval.items():
                    if isinstance(v, str):
                        lines.append(f'{k} = "{v}"')
                    elif isinstance(v, bool):
                        lines.append(f"{k} = {'true' if v else 'false'}")
                    elif isinstance(v, (int, float)):
                        lines.append(f"{k} = {v}")
            elif isinstance(fval, bool):
                lines.append(f"\n[features]")
                lines.append(f"{fname} = {'true' if fval else 'false'}")
    return ("\n".join(lines) + "\n") if lines else ""


def build_codex(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)

    # 1. 原样拷贝
    for name in ("auth.json", "models_cache.json"):
        f = src / name
        if f.exists():
            shutil.copy2(f, dst / name)
        else:
            _warn(f"{f} 不存在")

    # 1.5 hooks.json 拷贝 + 路径重写 (main 的 hook 机制: 全局 ~/.codex/hooks.json)
    #    容器里 hook 命令必须是容器内绝对路径；宿主机 home 前缀 → /root
    hooks = src / "hooks.json"
    if hooks.exists():
        text = hooks.read_text(encoding="utf-8")
        text = _rewrite_home_paths(text)
        # 统一重写到容器内 check_guidance.py (兼容仓库内任意历史位置)
        text = re.sub(
            r'python3\s+[^"\']*hooks/check_guidance\.py',
            "python3 /opt/ctf-agent/solver/hooks/check_guidance.py",
            text,
        )
        (dst / "hooks.json").write_text(text, encoding="utf-8")
    else:
        _warn(f"{hooks} 不存在 (容器内 codex 将无 hook，监督者指导无法注入)")

    # 2. 生成最小 config.toml (透传宿主的自定义 provider 段)
    extra = _extra_provider_sections(src)
    (dst / "config.toml").write_text(
        "# 容器专用最小配置 (cred_snapshot.py 生成，勿手改)\n"
        f'model = "{_read_model(src)}"\n'
        'model_reasoning_effort = "high"\n'
        + extra
        + "\n"
        '[projects."/opt/ctf-agent"]\n'
        'trust_level = "trusted"\n',
        encoding="utf-8",
    )


# ─── Hermes (§7.2) ───


def build_hermes(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("auth.json", "config.yaml", ".env"):
        f = src / name
        if not f.exists():
            _warn(f"{f} 不存在")
            continue
        if name == "auth.json":
            shutil.copy2(f, dst / name)  # 凭据文件原样
        else:
            (dst / name).write_text(
                f.read_text(encoding="utf-8").replace(
                    # 容器内 hermes 读挂载配置，路径无需重写；仅确保 host 路径不泄漏
                    str(src).replace("~", ""), "/home/ubuntu",
                )
            )

    # bin/ 同步 (tirith 安全扫描二进制, ~22MB): 不复制则容器内 hermes
    # 首次 terminal 调用要现场下载 GitHub release, 慢/被墙 -> warmup 超时崩溃
    # (2026-08-18 实测: 3 容器并发下载, 2 个 timeout 300 被杀, board 未初始化)
    bin_src = src / "bin"
    if bin_src.is_dir() and any(bin_src.iterdir()):
        shutil.copytree(bin_src, dst / "bin", dirs_exist_ok=True)
        _warn(f"bin/ 已同步 ({sum(f.stat().st_size for f in bin_src.rglob('*') if f.is_file())//1024//1024}MB)")
    # 用户安装的 skills (ctf-web 等) -- run.sh 的 hermes 调用带 -s ctf-web
    skills = src / "skills"
    if skills.is_dir():
        shutil.copytree(
            skills, dst / "skills",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"),
        )
    else:
        _warn(f"{skills} 不存在 (hermes 将无用户 skills，监督质量降级)")


# ─── Claude Code (§7.2) ───


def build_claude(src: Path, dst: Path) -> None:
    """快照 claude 配置: 复制宿主 ~/.claude/settings.json (env 段含 ANTHROPIC_*)。

    DockerBackend 容器注入 claude 引擎配置时优先读快照 claude/settings.json,
    这样比赛网关切换 (switch-api.sh gateway) 只改快照, 宿主 ~/.claude 保持官方。
    """
    dst.mkdir(parents=True, exist_ok=True)
    f = src / "settings.json"
    if not f.exists():
        _warn(f"{f} 不存在 (容器 claude 引擎将回退宿主配置)")
        return
    shutil.copy2(f, dst / "settings.json")


# ─── 入口 ───


def ensure_snapshot(
    out_root: Path = SNAPSHOT_ROOT,
    codex_home: Optional[Path] = None,
    hermes_home: Optional[Path] = None,
) -> Path:
    """生成快照，返回快照目录 (cred_snapshots/<run_id>)。"""
    run_id = time.strftime("run-%Y%m%d-%H%M%S")
    root = out_root / run_id
    build_codex(codex_home or HOME / ".codex", root / "codex")
    build_hermes(hermes_home or HOME / ".hermes", root / "hermes")
    build_claude(HOME / ".claude", root / "claude")

    # current 符号链接指向最新快照，方便排查 (DockerBackend 默认也用它)
    link = out_root / "current"
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(root.name)
    except OSError:
        pass

    print(f"[snapshot] 快照就绪: {root}")
    return root


def main() -> int:
    root = ensure_snapshot()
    print(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
