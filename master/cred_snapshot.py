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

    # 2. 生成最小 config.toml
    (dst / "config.toml").write_text(
        "# 容器专用最小配置 (cred_snapshot.py 生成，勿手改)\n"
        f'model = "{_read_model(src)}"\n'
        'model_reasoning_effort = "high"\n'
        "\n"
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
                _rewrite_home_paths(f.read_text(encoding="utf-8")), encoding="utf-8"
            )
    # 用户安装的 skills (如 ctf-supervisor-knowledge) -- run.sh 的 hermes 调用
    # 带 -s ctf-supervisor-knowledge，容器里没这个 skill 监督循环会降级
    skills = src / "skills"
    if skills.is_dir():
        shutil.copytree(
            skills, dst / "skills",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"),
        )
    else:
        _warn(f"{skills} 不存在 (hermes 将无用户 skills，监督质量降级)")


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
