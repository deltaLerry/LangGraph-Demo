from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Tuple

from storage import read_json, read_text_if_exists


def _fail(errors: List[str], msg: str) -> None:
    errors.append(msg)


def _must_exists(errors: List[str], path: str, label: str) -> bool:
    if not os.path.exists(path):
        _fail(errors, f"缺失：{label} -> {path}")
        return False
    return True


def _read_json_dict(errors: List[str], path: str, label: str) -> Dict[str, Any] | None:
    obj = read_json(path)
    if obj is None:
        _fail(errors, f"无法读取/解析 JSON：{label} -> {path}")
        return None
    return obj


def _check_canon(errors: List[str], project_dir: str) -> None:
    canon_dir = os.path.join(project_dir, "canon")
    _must_exists(errors, canon_dir, "canon 目录")

    world_path = os.path.join(canon_dir, "world.json")
    chars_path = os.path.join(canon_dir, "characters.json")
    timeline_path = os.path.join(canon_dir, "timeline.json")
    # style.md 已废弃：文风由“材料包（tone）”驱动；此处不再强制要求 canon/style.md

    if _must_exists(errors, world_path, "canon/world.json"):
        w = _read_json_dict(errors, world_path, "canon/world.json")
        if isinstance(w, dict):
            for k in ("rules", "factions", "places", "notes"):
                if k not in w:
                    _fail(errors, f"canon/world.json 缺少字段：{k}")
            for k in ("rules", "factions", "places"):
                if k in w and not isinstance(w.get(k), list):
                    _fail(errors, f"canon/world.json 字段类型应为 list：{k}")

    if _must_exists(errors, chars_path, "canon/characters.json"):
        c = _read_json_dict(errors, chars_path, "canon/characters.json")
        if isinstance(c, dict):
            arr = c.get("characters")
            if not isinstance(arr, list):
                _fail(errors, "canon/characters.json 字段 characters 应为 list")

    if _must_exists(errors, timeline_path, "canon/timeline.json"):
        t = _read_json_dict(errors, timeline_path, "canon/timeline.json")
        if isinstance(t, dict):
            arr = t.get("events")
            if not isinstance(arr, list):
                _fail(errors, "canon/timeline.json 字段 events 应为 list")

    # 不再检查 canon/style.md

    mem_dir = os.path.join(project_dir, "memory", "chapters")
    _must_exists(errors, mem_dir, "project memory/chapters 目录")


def _allowed_patch(it: Dict[str, Any]) -> Tuple[bool, str]:
    cp = it.get("canon_patch") if isinstance(it.get("canon_patch"), dict) else None
    if cp is None:
        return False, "缺少 canon_patch"
    target = str(cp.get("target", "") or "").strip()
    op = str(cp.get("op", "") or "").strip()
    path = str(cp.get("path", "") or "").strip()
    if target not in ("world.json", "characters.json", "timeline.json", "style.md"):
        return False, f"不支持 target={target}"
    if op not in ("note", "append"):
        return False, f"不支持 op={op}"
    # 约束：style.md 用 append；json 用 note/append（但 note 目前仅支持 notes）
    if target == "style.md" and op != "append":
        return False, "style.md 只支持 op=append"
    if op == "note" and path not in ("notes", "", "N/A"):
        return False, f"op=note 目前只支持 path=notes（实际={path}）"
    return True, ""


def _check_chapters(errors: List[str], current_dir: str) -> None:
    chapters_dir = os.path.join(current_dir, "chapters")
    if not _must_exists(errors, chapters_dir, "current/chapters 目录"):
        return

    md_ids: List[str] = []
    for name in os.listdir(chapters_dir):
        m = re.match(r"^(\d{3})\.md$", name)
        if m:
            md_ids.append(m.group(1))
    md_ids.sort()
    if not md_ids:
        _fail(errors, "current/chapters 下未找到任何 001.md 这样的章节文件")
        return

    for cid in md_ids:
        md_path = os.path.join(chapters_dir, f"{cid}.md")
        editor_md = os.path.join(chapters_dir, f"{cid}.editor.md")
        editor_json = os.path.join(chapters_dir, f"{cid}.editor.json")
        mem_json = os.path.join(chapters_dir, f"{cid}.memory.json")

        _must_exists(errors, md_path, f"chapters/{cid}.md")
        _must_exists(errors, editor_md, f"chapters/{cid}.editor.md")
        if not _must_exists(errors, editor_json, f"chapters/{cid}.editor.json"):
            continue

        report = _read_json_dict(errors, editor_json, f"chapters/{cid}.editor.json")
        if not isinstance(report, dict):
            continue
        decision = str(report.get("decision", "") or "").strip()
        if decision not in ("审核通过", "审核不通过"):
            _fail(errors, f"chapters/{cid}.editor.json decision 非法：{decision}")

        # memory：只要章节落盘，就必须存在（即使审核不通过/达到返工上限）
        if _must_exists(errors, mem_json, f"chapters/{cid}.memory.json（每章都必须存在）"):
            mem = _read_json_dict(errors, mem_json, f"chapters/{cid}.memory.json")
            if isinstance(mem, dict):
                if "summary" not in mem:
                    _fail(errors, f"chapters/{cid}.memory.json 缺少 summary")

        # suggestions：可选，但若存在必须结构合法且 patch 可应用
        for sug_name in (f"{cid}.canon_suggestions.json", f"{cid}.canon_update_suggestions.json"):
            sug_path = os.path.join(chapters_dir, sug_name)
            if not os.path.exists(sug_path):
                continue
            obj = _read_json_dict(errors, sug_path, f"chapters/{sug_name}")
            if not isinstance(obj, dict):
                continue
            items = obj.get("items")
            if not isinstance(items, list):
                _fail(errors, f"chapters/{sug_name} 缺少 items:list")
                continue
            for i, it in enumerate(items, start=1):
                if not isinstance(it, dict):
                    _fail(errors, f"chapters/{sug_name} items[{i}] 不是 dict")
                    continue
                ok, reason = _allowed_patch(it)
                if not ok:
                    _fail(errors, f"chapters/{sug_name} items[{i}] patch 不可应用：{reason}")


def _check_planner(errors: List[str], current_dir: str) -> None:
    planner_path = os.path.join(current_dir, "planner.json")
    if not _must_exists(errors, planner_path, "current/planner.json"):
        return
    obj = _read_json_dict(errors, planner_path, "current/planner.json")
    if not isinstance(obj, dict):
        return
    if "项目名称" not in obj:
        _fail(errors, "planner.json 缺少 项目名称")
    tasks = obj.get("任务列表")
    if not isinstance(tasks, list) or not tasks:
        _fail(errors, "planner.json 任务列表 为空或不是 list")


def main() -> int:
    p = argparse.ArgumentParser(description="阶段2验收：设定管理建设（Canon/Memory/EditorReport/建议补丁格式）")
    p.add_argument("--output-base", type=str, default="outputs", help="输出根目录（默认 outputs）")
    p.add_argument("--current-dir", type=str, default="", help="指定 current 输出目录（默认 <output-base>/current）")
    args = p.parse_args()

    output_base = os.path.abspath(args.output_base)
    current_dir = os.path.abspath(args.current_dir) if args.current_dir else os.path.join(output_base, "current")

    errors: List[str] = []

    if not _must_exists(errors, current_dir, "outputs/current 目录"):
        print("FAIL")
        for e in errors:
            print(f"- {e}")
        return 2

    run_meta_path = os.path.join(current_dir, "run_meta.json")
    if not _must_exists(errors, run_meta_path, "current/run_meta.json"):
        print("FAIL")
        for e in errors:
            print(f"- {e}")
        return 2

    meta = _read_json_dict(errors, run_meta_path, "current/run_meta.json") or {}
    rel_project_dir = str(meta.get("project_dir", "") or "").strip()
    if not rel_project_dir:
        _fail(errors, "run_meta.json 缺少 project_dir")
        project_dir = ""
    else:
        project_dir = os.path.join(output_base, rel_project_dir.replace("/", os.sep))

    # 1) Canon（项目知识库）
    if project_dir:
        _check_canon(errors, project_dir)

    # 2) current 章节产物 + editor_report + memory + suggestions
    _check_planner(errors, current_dir)
    _check_chapters(errors, current_dir)

    if errors:
        print("FAIL")
        for e in errors:
            print(f"- {e}")
        return 1

    print("PASS")
    print(f"- output_base: {output_base}")
    print(f"- current_dir: {current_dir}")
    print(f"- project_dir: {project_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


