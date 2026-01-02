from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from typing import Any, Dict, Optional, List, Tuple


def safe_filename(name: str, fallback: str = "project") -> str:
    name = (name or "").strip() or fallback
    # Windows 文件名非法字符：\ / : * ? " < > |
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:80] if len(name) > 80 else name


def make_run_dir(base_dir: str, project_name: Optional[str] = None) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    pn = safe_filename(project_name or "", fallback="story")
    run_dir = os.path.join(base_dir, f"{ts}-{pn}")
    os.makedirs(run_dir, exist_ok=True)
    return run_dir


def write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def write_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_json_if_exists(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _dir_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0.0


def _unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base = path
    i = 1
    while True:
        cand = f"{base}-dup{i}"
        if not os.path.exists(cand):
            return cand
        i += 1


def rotate_outputs(base_dir: str, new_run_dir: str, keep_last: int = 5) -> str:
    """
    目标：
    - 将本次输出目录命名为 base_dir/current
    - 仅保留最近 keep_last 次输出（包含 current）

    行为：
    - 若 base_dir/current 已存在：读取其 run_meta.json 中的 run_dir_name 并归档回 base_dir/<run_dir_name>
      归档名冲突则追加 -dupN。
    - 然后将 new_run_dir 重命名为 base_dir/current
    - 最后删除 base_dir 下除 current 外更老的目录，只保留 keep_last-1 个。
    """
    os.makedirs(base_dir, exist_ok=True)
    current_dir = os.path.join(base_dir, "current")

    # 1) 归档旧 current
    if os.path.exists(current_dir) and os.path.isdir(current_dir):
        meta = _read_json_if_exists(os.path.join(current_dir, "run_meta.json")) or {}
        prev_name = str(meta.get("run_dir_name") or "").strip()
        if not prev_name:
            prev_name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-previous"
        archive_path = _unique_path(os.path.join(base_dir, prev_name))
        os.replace(current_dir, archive_path)

    # 2) 将本次输出切换为 current
    if os.path.exists(current_dir):
        shutil.rmtree(current_dir, ignore_errors=True)
    os.replace(new_run_dir, current_dir)

    # 3) 保留最近 keep_last 次（current + keep_last-1 个归档）
    if keep_last <= 0:
        return current_dir

    dirs: List[Tuple[str, float]] = []
    for name in os.listdir(base_dir):
        p = os.path.join(base_dir, name)
        if not os.path.isdir(p):
            continue
        if name == "current":
            continue
        dirs.append((p, _dir_mtime(p)))

    dirs.sort(key=lambda x: x[1], reverse=True)
    keep = max(0, keep_last - 1)
    for p, _ in dirs[keep:]:
        shutil.rmtree(p, ignore_errors=True)

    return current_dir


