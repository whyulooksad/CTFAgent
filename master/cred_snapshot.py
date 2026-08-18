#!/usr/bin/env python3
"""
cred_snapshot.py -- 容器凭据"精制快照"生成器 (master-agent-spec.md §7.2)。

引擎切换 claude code 后 (2026-08-18)，解题引擎不再需要凭据快照：
claude code 通过环境变量接入赛方大模型平台 (llm.yaml -> ANTHROPIC_*，见
llm_config.py / run.sh)，DockerBackend 把 llm.yaml 只读挂载进容器即可。
旧引擎时代的 auth.json / ctf.config.toml / 最小 config.toml 全部退役。

剩下的快照只有 hermes 部分: 选择性拷贝 + home 路径重写 + skills 整目录。

输出: cred_snapshots/<run_id>/
  hermes/
    auth.json          原样
    config.yaml        home 前缀重写保险 (实测本机无绝对路径)
    .env               home 前缀重写保险
    skills/            用户安装的 skills 整目录 (ctf-supervisor-knowledge 等)

用法:
  python3 cred_snapshot.py            # 生成快照并打印路径
Master 集成: backend=docker 时 make_backend 自动调用 ensure_snapshot()。
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent          # master/
REPO_DIR = SCRIPT_DIR.parent                          # 仓库根
SNAPSHOT_ROOT = REPO_DIR / "cred_snapshots"
HOME = Path.home()


def _warn(msg: str) -> None:
    print(f"[snapshot] 警告: {msg}", file=sys.stderr)


def _rewrite_home_paths(text: str) -> str:
    """宿主机 home 前缀 → /root (host 无关)。"""
    return text.replace(str(HOME), "/root")


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
    hermes_home: Optional[Path] = None,
) -> Path:
    """生成快照，返回快照目录 (cred_snapshots/<run_id>)。"""
    run_id = time.strftime("run-%Y%m%d-%H%M%S")
    root = out_root / run_id
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
