from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from typing import Any, Dict, Optional, List, Tuple


def safe_filename(name: str, fallback: str = "project") -> str:
    name = (name or "").strip() or fallback
    # 规范化：去掉常见“书名号/引号”包裹，避免同名项目产生两个目录
    name = re.sub(r'^[《「『“"\']+', "", name)
    name = re.sub(r'[》」』”"\']+$', "", name)
    # Windows 文件名非法字符：\ / : * ? " < > |
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:80] if len(name) > 80 else name


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


## rotate_outputs 已删除：主流程统一用 make_current_dir() + archive_run()


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


def ensure_materials_files(project_dir: str) -> Dict[str, str]:
    """
    初始化“计划类材料”目录（阶段3）：只创建缺失文件，不覆盖已有内容。
    位置：projects/<project>/materials/
    """
    mdir = os.path.join(project_dir, "materials")
    os.makedirs(mdir, exist_ok=True)
    paths = {
        "outline": os.path.join(mdir, "outline.json"),
        "tone": os.path.join(mdir, "tone.json"),
    }
    if not os.path.exists(paths["outline"]):
        write_json(paths["outline"], {"main_arc": "", "themes": [], "chapters": [], "notes": ""})
    if not os.path.exists(paths["tone"]):
        write_json(
            paths["tone"],
            {"narration": "", "pacing": "", "reference_style": "", "style_constraints": [], "avoid": [], "notes": ""},
        )
    return paths


def load_materials_bundle(project_dir: str) -> Dict[str, Any]:
    """
    读取项目 materials（outline/tone），用于写作/复盘注入。
    注意：这是“计划类材料”，真值仍以 Canon 为准。
    """
    mdir = os.path.join(project_dir, "materials")
    return {
        "outline": read_json(os.path.join(mdir, "outline.json")) or {},
        "tone": read_json(os.path.join(mdir, "tone.json")) or {},
    }


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


def _split_list_like(s: str) -> List[str]:
    s = str(s or "").strip()
    if not s:
        return []
    # 常见分隔：中文顿号/逗号/分号/换行/斜杠
    for sep in ["\n", "；", ";", "、", "，", ",", "/", "|"]:
        s = s.replace(sep, " ")
    parts = [p.strip() for p in s.split(" ") if p.strip()]
    # 去重保持顺序
    seen = set()
    out: List[str] = []
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def normalize_canon_bundle(canon: Dict[str, Any]) -> Dict[str, Any]:
    """
    读时归一化 Canon（不做破坏性迁移，主要用于 prompt 注入/合并/upsert）。
    目标：把历史字段差异（detail/desc、string/list）统一成更稳定的形态。
    """
    c = canon if isinstance(canon, dict) else {}
    world = c.get("world") if isinstance(c.get("world"), dict) else {}
    characters = c.get("characters") if isinstance(c.get("characters"), dict) else {}
    timeline = c.get("timeline") if isinstance(c.get("timeline"), dict) else {}

    # world: rules/factions/places: 统一 desc 字段（兼容 detail）
    def _norm_named_arr(arr: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not isinstance(arr, list):
            return out
        for it in arr:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "") or "").strip()
            if not name:
                continue
            desc = str(it.get("desc", "") or "").strip()
            if not desc:
                desc = str(it.get("detail", "") or "").strip()
            out.append({"name": name, "desc": desc, **{k: v for k, v in it.items() if k not in ("detail",)}})
        return out

    world_n = dict(world)
    world_n["rules"] = _norm_named_arr(world.get("rules"))
    world_n["factions"] = _norm_named_arr(world.get("factions"))
    world_n["places"] = _norm_named_arr(world.get("places"))
    world_n["notes"] = str(world.get("notes", "") or "")

    # characters: 统一 traits/abilities/taboos 为 list，兼容旧字段 personality/abilities/taboos 的 string
    chars_arr = characters.get("characters")
    out_chars: List[Dict[str, Any]] = []
    if isinstance(chars_arr, list):
        for it in chars_arr:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "") or "").strip()
            if not name:
                continue
            traits = it.get("traits")
            if not isinstance(traits, list) or not traits:
                traits = _split_list_like(it.get("personality", ""))
            abilities = it.get("abilities")
            if not isinstance(abilities, list):
                abilities = _split_list_like(it.get("abilities", ""))
            taboos = it.get("taboos")
            if not isinstance(taboos, list):
                taboos = _split_list_like(it.get("taboos", ""))
            # relationships: 兼容 dict list -> string list
            rels: List[str] = []
            r = it.get("relationships")
            if isinstance(r, list):
                for rv in r:
                    if isinstance(rv, str) and rv.strip():
                        rels.append(rv.strip())
                    elif isinstance(rv, dict):
                        w = str(rv.get("with", "") or "").strip()
                        rel = str(rv.get("relation", "") or "").strip()
                        if w and rel:
                            rels.append(f"{w}:{rel}")
            out_chars.append(
                {
                    "name": name,
                    "traits": [str(x).strip() for x in traits if str(x).strip()],
                    "motivation": str(it.get("motivation", "") or ""),
                    "background": str(it.get("background", "") or ""),
                    "abilities": [str(x).strip() for x in abilities if str(x).strip()],
                    "taboos": [str(x).strip() for x in taboos if str(x).strip()],
                    "relationships": rels,
                    "notes": str(it.get("notes", "") or ""),
                }
            )
    characters_n = {"characters": out_chars}

    # timeline: 尽量统一 events 为 list[dict]，兼容 order/when/what/impact 或 chapter/event
    events = timeline.get("events")
    out_events: List[Dict[str, Any]] = []
    if isinstance(events, list):
        for it in events:
            if isinstance(it, dict) and it:
                out_events.append(dict(it))
    timeline_n = {"events": out_events}

    return {"world": world_n, "characters": characters_n, "timeline": timeline_n, "style": c.get("style", "")}


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


def _deep_get(obj: Any, path: str) -> Any:
    """
    极简路径访问：a.b.c 只支持 dict 层级（不支持数组索引）。
    """
    cur = obj
    for part in (path or "").split("."):
        part = part.strip()
        if not part:
            continue
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur.get(part)
    return cur


def _backup_file(path: str) -> str:
    """
    对单个文件做备份拷贝，返回备份路径。
    """
    if not os.path.exists(path):
        return ""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = f"{path}.bak.{ts}"
    shutil.copy2(path, bak)
    return bak


def read_canon_suggestions_from_dir(chapters_dir: str) -> List[Dict[str, Any]]:
    """
    读取 chapters/*canon_suggestions.json，并汇总为一个 list。
    约定：文件格式为 {"items":[...]}。
    """
    out: List[Dict[str, Any]] = []
    if not chapters_dir or not os.path.exists(chapters_dir):
        return out
    for name in os.listdir(chapters_dir):
        if not (name.endswith(".canon_suggestions.json") or name.endswith(".canon_update_suggestions.json")):
            continue
        p = os.path.join(chapters_dir, name)
        obj = read_json(p) or {}
        items = obj.get("items") if isinstance(obj.get("items"), list) else []
        for it in items:
            if isinstance(it, dict):
                out.append(it)
    return out


def read_materials_suggestions_from_dir(chapters_dir: str) -> List[Dict[str, Any]]:
    """
    读取 chapters/*materials_update_suggestions.json 并汇总为 list。
    约定：文件格式为 {"items":[...]}。
    """
    out: List[Dict[str, Any]] = []
    if not chapters_dir or not os.path.exists(chapters_dir):
        return out
    for name in os.listdir(chapters_dir):
        if not name.endswith(".materials_update_suggestions.json"):
            continue
        p = os.path.join(chapters_dir, name)
        obj = read_json(p) or {}
        items = obj.get("items") if isinstance(obj.get("items"), list) else []
        for it in items:
            if isinstance(it, dict):
                out.append(it)
    return out


def preview_materials_suggestions(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "（无）"
    lines: List[str] = []
    for i, it in enumerate(items, start=1):
        # 防御：只预览 materials_patch（若 action 存在且不是 materials_patch，则跳过）
        act = str(it.get("action", "") or "").strip()
        if act and act != "materials_patch":
            continue
        mp = it.get("materials_patch") if isinstance(it.get("materials_patch"), dict) else {}
        target = str(mp.get("target", "") or "N/A")
        op = str(mp.get("op", "") or "N/A")
        path = str(mp.get("path", "") or "N/A")
        issue = str(it.get("issue", "") or "").strip()
        lines.append(f"[{i}] target={target} op={op} path={path}")
        if issue:
            lines.append(f"    issue: {issue}")
    return "\n".join(lines).rstrip()


def apply_materials_suggestions(
    *,
    project_dir: str,
    items: List[Dict[str, Any]],
    yes: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    将 materials_update_suggestions 写回 projects/<project>/materials（计划类材料）。
    安全原则：本函数**只能写 materials 目录**，不允许触碰 Canon。

    支持：
    - target=outline.json：op=note(path=notes) / op=append(path=chapters, value=chapter dict 或 list；按 chapter_index upsert)
    - target=tone.json：op=note(path=notes) / op=append(path=style_constraints|avoid, value=str 或 list；幂等追加)
    """
    mdir = os.path.join(project_dir, "materials")
    outline_path = os.path.join(mdir, "outline.json")
    tone_path = os.path.join(mdir, "tone.json")
    ensure_materials_files(project_dir)

    stats = {"applied": 0, "skipped": 0, "backups": []}  # type: ignore[dict-item]

    def _backup(path: str) -> str:
        if not os.path.exists(path):
            return ""
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = f"{path}.bak.{ts}"
        shutil.copy2(path, bak)
        return bak

    def _append_unique(arr: List[Any], v: Any) -> None:
        if v is None:
            return
        if isinstance(v, list):
            for x in v:
                _append_unique(arr, x)
            return
        if v not in arr:
            arr.append(v)

    # 复用 canon 的保守合并能力（人物/规则等 upsert 逻辑）
    def _is_empty(v: Any) -> bool:
        if v is None:
            return True
        if isinstance(v, str):
            s = v.strip()
            return (s == "") or (s in ("N/A", "n/a", "待补充", "（待补充）", "（待明确）", "待明确", "未知"))
        if isinstance(v, (list, tuple, dict, set)):
            return len(v) == 0
        return False

    def _deep_merge_keep_old_on_empty(old: Any, new: Any) -> Any:
        if _is_empty(new):
            return old
        if isinstance(old, dict) and isinstance(new, dict):
            out = dict(old)
            for k, nv in new.items():
                ov = out.get(k)
                if isinstance(ov, dict) and isinstance(nv, dict):
                    out[k] = _deep_merge_keep_old_on_empty(ov, nv)
                elif isinstance(ov, list) and isinstance(nv, list):
                    out[k] = nv if nv else ov
                else:
                    out[k] = nv if not _is_empty(nv) else ov
            return out
        if isinstance(old, list) and isinstance(new, list):
            return new if new else old
        return new

    def _upsert_by_key(arr: List[Any], item: Any, *, key_fields: Tuple[str, ...]) -> bool:
        if not isinstance(item, dict):
            return False
        for k in key_fields:
            if k not in item or _is_empty(item.get(k)):
                return False
        for i, cur in enumerate(arr):
            if not isinstance(cur, dict):
                continue
            ok = True
            for k in key_fields:
                if cur.get(k) != item.get(k):
                    ok = False
                    break
            if not ok:
                continue
            arr[i] = _deep_merge_keep_old_on_empty(cur, item)
            return True
        arr.append(item)
        return True

    apply_all = bool(yes)
    quit_all = False
    for idx, it in enumerate(items, start=1):
        if quit_all:
            stats["skipped"] += 1
            continue
        # 防御：只允许应用 materials_patch（若 action 存在且不是 materials_patch，则跳过）
        act = str(it.get("action", "") or "").strip()
        if act and act != "materials_patch":
            stats["skipped"] += 1
            continue
        mp = it.get("materials_patch") if isinstance(it.get("materials_patch"), dict) else {}
        target = str(mp.get("target", "") or "").strip()
        op = str(mp.get("op", "") or "").strip()
        path = str(mp.get("path", "") or "").strip()
        value = mp.get("value", None)

        if not target or target == "N/A":
            stats["skipped"] += 1
            continue

        action = "apply" if apply_all else "ask"
        if action == "ask":
            while True:
                print(f"\n[{idx}/{len(items)}] target={target} op={op} path={path}")
                ans = input("选择：y(应用) s(跳过) a(全部应用) q(退出) > ").strip().lower()
                if ans in ("a", "all"):
                    apply_all = True
                    action = "apply"
                    break
                if ans in ("q", "quit", "exit"):
                    quit_all = True
                    action = "skip"
                    break
                if ans in ("s", "skip", "n", "no", ""):
                    action = "skip"
                    break
                if ans in ("y", "yes", "是", "确认"):
                    action = "apply"
                    break
                print("未识别指令。")

        if quit_all:
            stats["skipped"] += 1
            continue
        if action == "skip":
            stats["skipped"] += 1
            continue

        if dry_run:
            stats["applied"] += 1
            continue

        if target == "outline.json":
            obj = read_json(outline_path) or {}
            bak = _backup(outline_path)
            if bak:
                stats["backups"].append(bak)
            if op == "note":
                if (path or "notes") != "notes":
                    stats["skipped"] += 1
                    continue
                line = str(value if value is not None else "").strip()
                if not line:
                    stats["skipped"] += 1
                    continue
                prev = str(obj.get("notes", "") or "")
                if line not in prev:
                    obj["notes"] = (prev.rstrip() + ("\n" if prev.strip() else "") + line).strip()
            elif op == "append" and path == "chapters":
                arr = obj.get("chapters")
                if not isinstance(arr, list):
                    obj["chapters"] = []
                    arr = obj["chapters"]
                vals = value if isinstance(value, list) else [value]
                for v in vals:
                    if v is None:
                        continue
                    # chapters 以 chapter_index upsert
                    if _upsert_by_key(arr, v, key_fields=("chapter_index",)):
                        continue
                    if v not in arr:
                        arr.append(v)
            else:
                stats["skipped"] += 1
                continue
            write_json(outline_path, obj)
            stats["applied"] += 1
            continue

        if target == "tone.json":
            obj = read_json(tone_path) or {}
            bak = _backup(tone_path)
            if bak:
                stats["backups"].append(bak)
            if op == "note":
                if (path or "notes") != "notes":
                    stats["skipped"] += 1
                    continue
                line = str(value if value is not None else "").strip()
                if not line:
                    stats["skipped"] += 1
                    continue
                prev = str(obj.get("notes", "") or "")
                if line not in prev:
                    obj["notes"] = (prev.rstrip() + ("\n" if prev.strip() else "") + line).strip()
            elif op == "append" and path in ("style_constraints", "avoid"):
                arr = obj.get(path)
                if not isinstance(arr, list):
                    obj[path] = []
                    arr = obj[path]
                _append_unique(arr, value)
            else:
                stats["skipped"] += 1
                continue
            write_json(tone_path, obj)
            stats["applied"] += 1
            continue

        stats["skipped"] += 1

    return stats


def preview_canon_suggestions(items: List[Dict[str, Any]]) -> str:
    """
    将建议格式化为可读文本，便于 CLI 打印与确认。
    """
    if not items:
        return "（无）"
    lines: List[str] = []
    for i, it in enumerate(items, start=1):
        # 防御：只预览 canon_patch（若 action 存在且不是 canon_patch，则跳过）
        act = str(it.get("action", "") or "").strip()
        if act and act != "canon_patch":
            continue
        cp = it.get("canon_patch") if isinstance(it.get("canon_patch"), dict) else {}
        target = str(cp.get("target", "") or "N/A")
        op = str(cp.get("op", "") or "N/A")
        path = str(cp.get("path", "") or "N/A")
        val = cp.get("value", "N/A")
        issue = str(it.get("issue", "") or "").strip()
        quote = str(it.get("quote", "") or "").strip()
        lines.append(f"[{i}] target={target} op={op} path={path}")
        if issue:
            lines.append(f"    issue: {issue}")
        if quote:
            lines.append(f"    quote: {quote}")
        # value 可能很长，截断
        try:
            s = json.dumps(val, ensure_ascii=False) if not isinstance(val, str) else val
        except Exception:
            s = str(val)
        if len(s) > 240:
            s = s[:220].rstrip() + "…"
        lines.append(f"    value: {s}")
    return "\n".join(lines).rstrip()


def apply_canon_suggestions(
    *,
    project_dir: str,
    items: List[Dict[str, Any]],
    yes: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    将 editor 产出的 canon_suggestions 以“确认后应用”的方式写回 Canon。

    当前只支持最保守的 patch 行为（避免误伤）：
    - target=world.json / characters.json / timeline.json：仅支持 op=note（写入 notes） 或 op=append（追加到数组字段）
    - target=style.md：op=append（追加到文件末尾，以 bullet 形式）

    返回：统计信息与备份路径列表。
    """
    canon_dir = os.path.join(project_dir, "canon")
    world_path = os.path.join(canon_dir, "world.json")
    characters_path = os.path.join(canon_dir, "characters.json")
    timeline_path = os.path.join(canon_dir, "timeline.json")
    style_path = os.path.join(canon_dir, "style.md")

    stats = {"applied": 0, "skipped": 0, "backups": []}  # type: ignore[dict-item]

    def _is_empty(v: Any) -> bool:
        if v is None:
            return True
        if isinstance(v, str):
            s = v.strip()
            return (s == "") or (s in ("N/A", "n/a", "待补充", "（待补充）", "（待明确）", "待明确", "未知"))
        if isinstance(v, (list, tuple, dict, set)):
            return len(v) == 0
        return False

    def _deep_merge_keep_old_on_empty(old: Any, new: Any) -> Any:
        """
        用于 Canon“增量补全”的保守合并：
        - dict: 递归合并；new 为空则不覆盖 old
        - list: new 非空则替换（避免 list 合并策略过度复杂）
        - scalar: new 非空则覆盖，否则保留 old
        """
        if _is_empty(new):
            return old
        if isinstance(old, dict) and isinstance(new, dict):
            out = dict(old)
            for k, nv in new.items():
                ov = out.get(k)
                if isinstance(ov, dict) and isinstance(nv, dict):
                    out[k] = _deep_merge_keep_old_on_empty(ov, nv)
                elif isinstance(ov, list) and isinstance(nv, list):
                    out[k] = nv if nv else ov
                else:
                    out[k] = nv if not _is_empty(nv) else ov
            return out
        if isinstance(old, list) and isinstance(new, list):
            return new if new else old
        return new

    def _upsert_by_key(arr: List[Any], item: Any, *, key_fields: Tuple[str, ...]) -> bool:
        """
        在数组中按 key_fields 匹配 dict 并合并（用于人物/规则/势力等“同名更新”）。
        返回 True 表示写入/更新成功；False 表示无法处理。
        """
        if not isinstance(item, dict):
            return False
        # 必须包含全部 key
        for k in key_fields:
            if k not in item or _is_empty(item.get(k)):
                return False
        # 查找匹配项
        for i, cur in enumerate(arr):
            if not isinstance(cur, dict):
                continue
            ok = True
            for k in key_fields:
                if cur.get(k) != item.get(k):
                    ok = False
                    break
            if not ok:
                continue
            # 合并更新
            arr[i] = _deep_merge_keep_old_on_empty(cur, item)
            return True
        # 不存在则追加
        arr.append(item)
        return True

    def _print_help() -> None:
        print(
            "\n交互指令：\n"
            "- y : 应用本条\n"
            "- s : 跳过本条\n"
            "- a : 应用全部剩余\n"
            "- p : 打印本条详细信息（含 canon_patch/value）\n"
            "- q : 退出（后续条目全部跳过）\n"
            "- ? : 显示帮助\n"
        )

    apply_all_remaining = bool(yes)
    quit_all = False

    for idx, it in enumerate(items, start=1):
        if quit_all:
            stats["skipped"] += 1
            continue
        # 防御：只允许应用 canon_patch（若 action 存在且不是 canon_patch，则跳过）
        act = str(it.get("action", "") or "").strip()
        if act and act != "canon_patch":
            stats["skipped"] += 1
            continue
        cp = it.get("canon_patch") if isinstance(it.get("canon_patch"), dict) else {}
        target = str(cp.get("target", "") or "").strip()
        op = str(cp.get("op", "") or "").strip()
        path = str(cp.get("path", "") or "").strip()
        value = cp.get("value", None)

        if not target or target == "N/A":
            stats["skipped"] += 1
            continue

        # 每条建议逐条确认（更人性化：支持 a/y/s/p/q/?）
        action = "apply" if apply_all_remaining else "ask"
        if action == "ask":
            while True:
                print(f"\n[{idx}/{len(items)}] target={target} op={op} path={path}")
                ans = input("选择：y(应用) s(跳过) a(全部应用) p(详情) q(退出) ?(帮助) > ").strip().lower()
                if ans in ("?", "h", "help"):
                    _print_help()
                    continue
                if ans in ("p", "print"):
                    try:
                        print(json.dumps(it, ensure_ascii=False, indent=2))
                    except Exception:
                        print(str(it))
                    continue
                if ans in ("a", "all"):
                    apply_all_remaining = True
                    action = "apply"
                    break
                if ans in ("q", "quit", "exit"):
                    quit_all = True
                    action = "skip"
                    break
                if ans in ("s", "skip", "n", "no", ""):
                    action = "skip"
                    break
                if ans in ("y", "yes", "是", "确认"):
                    action = "apply"
                    break
                print("未识别指令，输入 ? 查看帮助。")

        if quit_all:
            stats["skipped"] += 1
            continue
        if action == "skip":
            stats["skipped"] += 1
            continue

        if dry_run:
            stats["applied"] += 1
            continue

        if target == "style.md":
            bak = _backup_file(style_path)
            if bak:
                stats["backups"].append(bak)
            old = read_text_if_exists(style_path)
            line = str(value if value is not None else "").strip()
            if not line:
                stats["skipped"] += 1
                continue
            bullet = line if line.startswith("- ") else f"- {line}"
            new = (old.rstrip() + "\n" + bullet + "\n").lstrip("\n")
            write_text(style_path, new)
            stats["applied"] += 1
            continue

        # JSON targets
        if target == "world.json":
            obj = read_json(world_path) or {}
            bak = _backup_file(world_path)
            if bak:
                stats["backups"].append(bak)
        elif target == "characters.json":
            obj = read_json(characters_path) or {}
            bak = _backup_file(characters_path)
            if bak:
                stats["backups"].append(bak)
        elif target == "timeline.json":
            obj = read_json(timeline_path) or {}
            bak = _backup_file(timeline_path)
            if bak:
                stats["backups"].append(bak)
        else:
            stats["skipped"] += 1
            continue

        if op == "note":
            # path 为空则默认写到 notes
            key = path or "notes"
            if key != "notes":
                # 目前只支持 notes，避免复杂 path 修改
                stats["skipped"] += 1
                continue
            prev = str(obj.get("notes", "") or "")
            line = str(value if value is not None else "").strip()
            if not line:
                stats["skipped"] += 1
                continue
            if line not in prev:
                obj["notes"] = (prev.rstrip() + ("\n" if prev.strip() else "") + line).strip()
            # 写回
        elif op == "append":
            # append 到数组字段（如 rules/factions/places/events/characters）
            arr = _deep_get(obj, path)
            if not isinstance(arr, list):
                stats["skipped"] += 1
                continue
            # value 可能本身就是 list（例如 editor 一次性给出多条 rules/events/characters）。
            # 这里做“幂等 + 可增量更新（upsert）”：
            # - 对 dict 且包含关键字段（如 name / chapter+event），视为同一实体的补全更新
            # - 否则按深度相等去重追加
            items_to_apply = value if isinstance(value, list) else [value]
            for v in items_to_apply:
                if v is None:
                    continue
                # characters.json: characters 按 name upsert
                if target == "characters.json" and path == "characters":
                    if _upsert_by_key(arr, v, key_fields=("name",)):
                        continue
                # world.json: rules/factions/places 按 name upsert
                if target == "world.json" and path in ("rules", "factions", "places"):
                    if _upsert_by_key(arr, v, key_fields=("name",)):
                        continue
                # timeline.json: events 按 (chapter,event) upsert；否则退化为 name
                if target == "timeline.json" and path == "events":
                    if _upsert_by_key(arr, v, key_fields=("chapter", "event")):
                        continue
                    if _upsert_by_key(arr, v, key_fields=("name",)):
                        continue

                # fallback：幂等追加
                if v not in arr:
                    arr.append(v)
        else:
            stats["skipped"] += 1
            continue

        if target == "world.json":
            write_json(world_path, obj)
        elif target == "characters.json":
            write_json(characters_path, obj)
        else:
            write_json(timeline_path, obj)
        stats["applied"] += 1

    return stats


