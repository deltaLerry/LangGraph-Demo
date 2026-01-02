from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from state import StoryState
from storage import load_canon_bundle, read_json, write_json, write_text


def _append_note(existing: str, line: str) -> str:
    existing = (existing or "").strip()
    line = (line or "").strip()
    if not line:
        return existing
    if not existing:
        return line
    # 去重：完全相同的行不重复追加
    if line in existing.splitlines():
        return existing
    return (existing.rstrip() + "\n" + line).strip()


def _ensure_list_of_dict(obj: Any) -> List[Dict[str, Any]]:
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    return []


def _upsert_named(items: List[Dict[str, Any]], *, name: str, detail: str, detail_key: str = "detail") -> bool:
    """
    在 rules/factions/places 这类结构中按 name 去重：
    - 已存在同名：若 detail 为空则补全；否则不覆盖
    - 不存在：追加
    返回是否发生变更
    """
    name = (name or "").strip()
    detail = (detail or "").strip()
    if not name:
        return False
    for it in items:
        if str(it.get("name", "") or "").strip() == name:
            if detail and (it.get(detail_key) in (None, "", [], {})):
                it[detail_key] = detail
                return True
            return False
    items.append({"name": name, detail_key: detail})
    return True


def _find_character(characters: List[Dict[str, Any]], name: str) -> Dict[str, Any] | None:
    name = (name or "").strip()
    if not name:
        return None
    for c in characters:
        if str(c.get("name", "") or "").strip() == name:
            return c
    return None


def _guess_character_name_from_key(key: str, known_names: List[str]) -> str:
    k = (key or "").strip()
    if not k:
        return ""
    # 简单启发：key 中包含已知人物名则归因给该人物（优先最长匹配）
    hits = [n for n in known_names if n and n in k]
    if not hits:
        return ""
    hits.sort(key=len, reverse=True)
    return hits[0]


def canon_update_agent(state: StoryState) -> StoryState:
    """
    阶段2：Canon 增量更新（从“审核通过后的 chapter memory”沉淀回设定）。

    设计原则：
    - 只在 editor == 审核通过 时生效（否则不写入，避免用“失败稿”污染设定）
    - 尽量“追加/补全”，不覆盖人工维护内容
    - item 等不在 schema 的事实，写入 world.notes（可追溯、可检索）
    """
    logger = state.get("logger")
    chapter_index = int(state.get("chapter_index", 1))
    if logger:
        logger.event("node_start", node="canon_update", chapter_index=chapter_index)

    if str(state.get("editor_decision", "") or "") != "审核通过":
        state["canon_update_used"] = False
        if logger:
            logger.event("node_end", node="canon_update", chapter_index=chapter_index, skipped=True, reason="editor_not_pass")
        return state

    project_dir = str(state.get("project_dir", "") or "")
    if not project_dir:
        state["canon_update_used"] = False
        if logger:
            logger.event("node_end", node="canon_update", chapter_index=chapter_index, skipped=True, reason="missing_project_dir")
        return state

    mem = state.get("chapter_memory") or {}
    if not isinstance(mem, dict) or not mem:
        state["canon_update_used"] = False
        if logger:
            logger.event("node_end", node="canon_update", chapter_index=chapter_index, skipped=True, reason="missing_chapter_memory")
        return state

    # 读取 Canon（四件套）
    canon = load_canon_bundle(project_dir)
    world = canon.get("world") if isinstance(canon.get("world"), dict) else {}
    characters_obj = canon.get("characters") if isinstance(canon.get("characters"), dict) else {}
    timeline = canon.get("timeline") if isinstance(canon.get("timeline"), dict) else {}
    style = str(canon.get("style", "") or "")

    # ---- world.json ----
    rules = _ensure_list_of_dict(world.get("rules"))
    factions = _ensure_list_of_dict(world.get("factions"))
    places = _ensure_list_of_dict(world.get("places"))
    notes = str(world.get("notes", "") or "")

    new_facts = mem.get("new_facts") if isinstance(mem.get("new_facts"), list) else []
    facts: List[Dict[str, str]] = []
    for it in new_facts:
        if not isinstance(it, dict):
            continue
        t = str(it.get("type", "") or "").strip()
        k = str(it.get("key", "") or "").strip()
        v = str(it.get("value", "") or "").strip()
        if t and k and v:
            facts.append({"type": t, "key": k, "value": v})

    world_changed = False
    timeline_changed = False
    characters_changed = False
    style_changed = False

    # 先准备人物名列表（用于把“character 类型 new_facts”归因到具体人物）
    characters_list = _ensure_list_of_dict(characters_obj.get("characters"))
    known_names = [str(c.get("name", "") or "").strip() for c in characters_list if str(c.get("name", "") or "").strip()]

    for f in facts:
        t = f["type"]
        k = f["key"]
        v = f["value"]
        if t in ("world", "rule"):
            # world -> rule：尽量结构化
            world_changed = _upsert_named(rules, name=k, detail=v) or world_changed
        elif t == "faction":
            world_changed = _upsert_named(factions, name=k, detail=v) or world_changed
        elif t == "place":
            world_changed = _upsert_named(places, name=k, detail=v) or world_changed
        elif t == "timeline":
            # 极简：追加到 timeline.events（仅当明确标注 timeline 类型）
            events = _ensure_list_of_dict(timeline.get("events"))
            max_order = 0
            for e in events:
                try:
                    max_order = max(max_order, int(e.get("order", 0) or 0))
                except Exception:
                    continue
            events.append(
                {
                    "order": max_order + 1,
                    "when": f"第{chapter_index}章（沉淀）",
                    "what": f"{k}：{v}",
                    "impact": "",
                }
            )
            timeline["events"] = events
            timeline_changed = True
        elif t == "character":
            # 尽量归因到具体角色，否则写入 world.notes（不破坏角色 schema）
            who = _guess_character_name_from_key(k, known_names)
            if who:
                c = _find_character(characters_list, who)
                if c is not None:
                    c["notes"] = _append_note(str(c.get("notes", "") or ""), f"[第{chapter_index}章] {k}：{v}")
                    characters_changed = True
            else:
                notes = _append_note(notes, f"[第{chapter_index}章][角色事实] {k}：{v}")
                world_changed = True
        else:
            # item / other：写进 world.notes，保证可追溯
            notes = _append_note(notes, f"[第{chapter_index}章][{t}] {k}：{v}")
            world_changed = True

    world["rules"] = rules
    world["factions"] = factions
    world["places"] = places
    world["notes"] = notes

    # ---- characters.json ----
    # 从 character_updates 把“状态/新增信息”沉淀到对应角色 notes（不存在则新增一个最小人物条目）
    cu = mem.get("character_updates") if isinstance(mem.get("character_updates"), list) else []
    for it in cu:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name", "") or "").strip()
        status = str(it.get("status", "") or "").strip()
        new_info = str(it.get("new_info", "") or "").strip()
        # 空更新不落盘：避免模板模式/脏数据污染 Canon
        if not status and not new_info:
            continue
        if not name:
            continue
        c = _find_character(characters_list, name)
        if c is None:
            c = {
                "name": name,
                "role": "（新增）",
                "personality": "",
                "motivation": "",
                "abilities": "",
                "taboos": "",
                "relationships": [],
            }
            characters_list.append(c)
            if name not in known_names:
                known_names.append(name)
            characters_changed = True
        line = f"[第{chapter_index}章] {status}；{new_info}".strip("； ").strip()
        if not line or line == f"[第{chapter_index}章]":
            continue
        c["notes"] = _append_note(str(c.get("notes", "") or ""), line)
        characters_changed = True

    characters_obj["characters"] = characters_list

    # ---- style.md ----
    style_notes = mem.get("style_notes") if isinstance(mem.get("style_notes"), list) else []
    style_lines = [str(x).strip() for x in style_notes if str(x).strip()]
    if style_lines:
        header = "## 增量（自动沉淀）"
        if header not in style:
            style = (style.rstrip() + "\n\n" + header + "\n").strip() + "\n"
            style_changed = True
        for s in style_lines:
            bullet = f"- {s}"
            if bullet not in style:
                style = style.rstrip() + "\n" + bullet
                style_changed = True
        style = style.strip() + "\n"

    # ---- 落盘：仅在变更时写入 ----
    canon_dir = os.path.join(project_dir, "canon")
    if world_changed:
        write_json(os.path.join(canon_dir, "world.json"), world)
    if characters_changed:
        write_json(os.path.join(canon_dir, "characters.json"), characters_obj)
    if timeline_changed:
        write_json(os.path.join(canon_dir, "timeline.json"), timeline)
    if style_changed:
        write_text(os.path.join(canon_dir, "style.md"), style)

    state["canon_update_used"] = bool(world_changed or characters_changed or timeline_changed or style_changed)
    if logger:
        logger.event(
            "node_end",
            node="canon_update",
            chapter_index=chapter_index,
            used=True,
            world_changed=world_changed,
            characters_changed=characters_changed,
            timeline_changed=timeline_changed,
            style_changed=style_changed,
            facts_count=len(facts),
            character_updates_count=len(cu) if isinstance(cu, list) else 0,
            style_notes_count=len(style_lines),
        )
    return state


