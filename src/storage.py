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


def read_json(path: str) -> Optional[Dict[str, Any]]:
    """
    读取 JSON（不存在/解析失败返回 None）
    """
    return _read_json_if_exists(path)


def read_text_if_exists(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


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
    【已不再推荐在主流程使用】
    当前主流程采用 make_current_dir() + archive_run()：
    - outputs/current：仅保留一次尝试（覆盖写入）
    - outputs/projects/<project>/stages/<stage>/runs/<run_id>/：持久化归档

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


def make_current_dir(base_dir: str) -> str:
    """
    “尝试目录”：只保留一个 current 目录，每次运行覆盖写入。
    """
    os.makedirs(base_dir, exist_ok=True)
    current_dir = os.path.join(base_dir, "current")
    if os.path.exists(current_dir):
        shutil.rmtree(current_dir, ignore_errors=True)
    os.makedirs(current_dir, exist_ok=True)
    return current_dir


def get_project_dir(base_dir: str, project_name: str) -> str:
    """
    “持久化目录”：用于长期记忆（Canon / chapter memory / stage归档）。
    """
    projects_root = os.path.join(base_dir, "projects")
    os.makedirs(projects_root, exist_ok=True)
    slug = safe_filename(project_name, fallback="story")
    pdir = os.path.join(projects_root, slug)
    os.makedirs(pdir, exist_ok=True)
    return pdir


def ensure_canon_files(project_dir: str) -> Dict[str, str]:
    """
    初始化 Canon 三件套（+ style），只创建缺失文件，不覆盖已有内容。
    """
    canon_dir = os.path.join(project_dir, "canon")
    os.makedirs(canon_dir, exist_ok=True)

    paths = {
        "world": os.path.join(canon_dir, "world.json"),
        "characters": os.path.join(canon_dir, "characters.json"),
        "timeline": os.path.join(canon_dir, "timeline.json"),
        "style": os.path.join(canon_dir, "style.md"),
    }

    if not os.path.exists(paths["world"]):
        write_json(paths["world"], {"rules": [], "factions": [], "places": [], "notes": ""})
    if not os.path.exists(paths["characters"]):
        write_json(paths["characters"], {"characters": []})
    if not os.path.exists(paths["timeline"]):
        write_json(paths["timeline"], {"events": []})
    if not os.path.exists(paths["style"]):
        write_text(
            paths["style"],
            "\n".join(
                [
                    "# 文风约束（可编辑）",
                    "",
                    "- 叙述视角：第三人称/第一人称（按项目选择）",
                    "- 节奏：短句为主，关键场景拉长描写",
                    "- 禁止：AI味总结句、机械重复句式、无意义的套话",
                    "",
                    "（你可以把你喜欢的网文片段特征写在这里，后续写作会注入。）",
                    "",
                ]
            ),
        )
    return paths


def ensure_memory_dirs(project_dir: str) -> Dict[str, str]:
    """
    初始化记忆目录：chapter memory / arc summaries（后续阶段用）。
    """
    mem_root = os.path.join(project_dir, "memory")
    chapters_dir = os.path.join(mem_root, "chapters")
    arcs_dir = os.path.join(mem_root, "arcs")
    os.makedirs(chapters_dir, exist_ok=True)
    os.makedirs(arcs_dir, exist_ok=True)
    return {"memory_root": mem_root, "chapters_dir": chapters_dir, "arcs_dir": arcs_dir}


def load_canon_bundle(project_dir: str) -> Dict[str, Any]:
    """
    读取 Canon 四件套（world/characters/timeline/style），用于写作/审核注入。
    """
    canon_dir = os.path.join(project_dir, "canon")
    return {
        "world": read_json(os.path.join(canon_dir, "world.json")) or {},
        "characters": read_json(os.path.join(canon_dir, "characters.json")) or {},
        "timeline": read_json(os.path.join(canon_dir, "timeline.json")) or {},
        "style": read_text_if_exists(os.path.join(canon_dir, "style.md")),
    }


def load_recent_chapter_memories(
    project_dir: str, *, before_chapter: int, k: int = 3
) -> List[Dict[str, Any]]:
    """
    读取最近 k 章的 chapter memory（从 projects/<project>/memory/chapters 下取）。
    - before_chapter：当前章号；会读取 < before_chapter 的历史记忆
    - 只读取存在且能解析为 dict 的文件
    """
    mem_dir = os.path.join(project_dir, "memory", "chapters")
    if not os.path.exists(mem_dir):
        return []

    # 从 before_chapter-1 倒序找
    out: List[Dict[str, Any]] = []
    for idx in range(before_chapter - 1, 0, -1):
        if len(out) >= max(0, int(k)):
            break
        name = f"{idx:03d}.memory.json"
        p = os.path.join(mem_dir, name)
        obj = read_json(p)
        if isinstance(obj, dict) and obj:
            out.append(obj)
    return out


def build_recent_memory_synopsis(memories: List[Dict[str, Any]]) -> str:
    """
    将最近章节记忆压缩成“梗概串”，避免把完整 memory JSON 塞进 prompt。
    """
    if not memories:
        return "（无）"
    lines: List[str] = []
    # memories 是倒序（最近在前），阅读更顺畅则反转为时间顺序
    for m in reversed(memories):
        chap = m.get("chapter_index", "")
        summary = str(m.get("summary", "") or "").strip()
        if not summary:
            continue
        # 控制每章梗概长度，避免 prompt 膨胀
        if len(summary) > 280:
            summary = summary[:260].rstrip() + "…"
        lines.append(f"- 第{chap}章：{summary}")
    return "\n".join(lines).strip() or "（无）"


def get_max_chapter_memory_index(project_dir: str) -> int:
    """
    从 projects/<project>/memory/chapters/*.memory.json 推断已有最大章号。
    返回 0 表示未找到。
    """
    mem_dir = os.path.join(project_dir, "memory", "chapters")
    if not os.path.exists(mem_dir):
        return 0
    max_idx = 0
    try:
        for name in os.listdir(mem_dir):
            if not name.endswith(".memory.json"):
                continue
            # 支持 001.memory.json / 101.memory.json
            m = re.match(r"^(\d+)\.memory\.json$", name)
            if not m:
                continue
            try:
                idx = int(m.group(1))
            except ValueError:
                continue
            if idx > max_idx:
                max_idx = idx
    except Exception:
        return 0
    return max_idx


def archive_run(
    *,
    base_dir: str,
    project_dir: str,
    stage: str,
    current_dir: str,
    run_id: Optional[str] = None,
) -> str:
    """
    将本次 current 目录“复制归档”到项目的 stages 目录下：
    outputs/projects/<project>/stages/<stage>/runs/<run_id>/
    并更新 stage_index.json
    """
    stage_name = safe_filename(stage or "stage1", fallback="stage1")
    if not run_id:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    stages_dir = os.path.join(project_dir, "stages", stage_name, "runs")
    os.makedirs(stages_dir, exist_ok=True)
    dst = _unique_path(os.path.join(stages_dir, run_id))
    shutil.copytree(current_dir, dst, dirs_exist_ok=True)

    # stage_index.json：记录每次 run 的元信息（可追溯）
    index_path = os.path.join(project_dir, "stage_index.json")
    index = _read_json_if_exists(index_path) or {"stages": {}}
    stages = index.get("stages") if isinstance(index.get("stages"), dict) else {}
    items = stages.get(stage_name) if isinstance(stages.get(stage_name), list) else []
    items.append(
        {
            "run_id": os.path.basename(dst),
            "archived_at": datetime.now().isoformat(timespec="seconds"),
            "path": os.path.relpath(dst, base_dir).replace("\\", "/"),
        }
    )
    stages[stage_name] = items
    index["stages"] = stages
    write_json(index_path, index)

    return dst


