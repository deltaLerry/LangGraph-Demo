from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from debug_log import truncate_text


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _as_str(x: Any) -> str:
    return str(x or "").strip()


def _as_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _as_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def default_world() -> Dict[str, Any]:
    return {"rules": [], "factions": [], "places": [], "notes": ""}


def default_characters() -> Dict[str, Any]:
    return {"characters": []}


def default_outline() -> Dict[str, Any]:
    return {"main_arc": "", "themes": [], "chapters": []}


def default_tone() -> Dict[str, Any]:
    return {
        "narration": "",
        "pacing": "",
        "style_constraints": [],
        "avoid": [],
        "reference_style": "",
    }


def ensure_world(obj: Any) -> Dict[str, Any]:
    d = _as_dict(obj)
    out = default_world()
    for k in ("rules", "factions", "places"):
        v = d.get(k)
        out[k] = v if isinstance(v, list) else []
    out["notes"] = _as_str(d.get("notes", ""))
    return out


def ensure_characters(obj: Any) -> Dict[str, Any]:
    d = _as_dict(obj)
    arr = d.get("characters")
    out_arr: List[Dict[str, Any]] = []
    if isinstance(arr, list):
        for it in arr:
            if not isinstance(it, dict):
                continue
            name = _as_str(it.get("name", ""))
            if not name:
                continue
            out_arr.append(
                {
                    "name": name,
                    "traits": _as_list(it.get("traits")),
                    "motivation": _as_str(it.get("motivation", "")),
                    "background": _as_str(it.get("background", "")),
                    "abilities": _as_list(it.get("abilities")),
                    "taboos": _as_list(it.get("taboos")),
                    "relationships": _as_list(it.get("relationships")),
                    "notes": _as_str(it.get("notes", "")),
                }
            )
    return {"characters": out_arr}


def ensure_outline(obj: Any) -> Dict[str, Any]:
    d = _as_dict(obj)
    out = default_outline()
    out["main_arc"] = _as_str(d.get("main_arc", ""))
    out["themes"] = [str(x).strip() for x in _as_list(d.get("themes")) if str(x).strip()]

    chapters = d.get("chapters")
    out_chaps: List[Dict[str, Any]] = []
    if isinstance(chapters, list):
        for it in chapters:
            if not isinstance(it, dict):
                continue
            try:
                idx = int(it.get("chapter_index", 0) or 0)
            except Exception:
                idx = 0
            title = _as_str(it.get("title", ""))
            beats = [str(x).strip() for x in _as_list(it.get("beats")) if str(x).strip()]
            out_chaps.append(
                {
                    "chapter_index": idx,
                    "title": title,
                    "goal": _as_str(it.get("goal", "")),
                    "conflict": _as_str(it.get("conflict", "")),
                    "beats": beats,
                    "ending_hook": _as_str(it.get("ending_hook", "")),
                }
            )
    out["chapters"] = out_chaps
    return out


def ensure_tone(obj: Any) -> Dict[str, Any]:
    d = _as_dict(obj)
    out = default_tone()
    out["narration"] = _as_str(d.get("narration", ""))
    out["pacing"] = _as_str(d.get("pacing", ""))
    out["reference_style"] = _as_str(d.get("reference_style", ""))
    out["style_constraints"] = [str(x).strip() for x in _as_list(d.get("style_constraints")) if str(x).strip()]
    out["avoid"] = [str(x).strip() for x in _as_list(d.get("avoid")) if str(x).strip()]
    return out


def build_materials_bundle(
    *,
    project_name: str,
    idea: str,
    world: Any,
    characters: Any,
    outline: Any,
    tone: Any,
    version: str = "stage3_v1",
) -> Dict[str, Any]:
    return {
        "version": version,
        "generated_at": _now_iso(),
        "project_name": _as_str(project_name),
        "idea": _as_str(idea),
        "world": ensure_world(world),
        "characters": ensure_characters(characters),
        "outline": ensure_outline(outline),
        "tone": ensure_tone(tone),
    }


def pick_outline_for_chapter(bundle: Dict[str, Any], chapter_index: int) -> Dict[str, Any]:
    """
    从 materials_bundle.outline.chapters 中选取指定章的细纲（找不到则返回空结构）。
    """
    outline = _as_dict(bundle.get("outline"))
    chapters = outline.get("chapters")
    if not isinstance(chapters, list):
        return {}
    for it in chapters:
        if not isinstance(it, dict):
            continue
        try:
            idx = int(it.get("chapter_index", 0) or 0)
        except Exception:
            idx = 0
        if idx == int(chapter_index):
            return it
    return {}


def materials_prompt_digest(bundle: Dict[str, Any], *, chapter_index: Optional[int] = None) -> str:
    """
    将材料包压缩成适合注入 prompt 的摘要（避免 prompt 膨胀）。
    - chapter_index 提供时，会额外抽取该章细纲。
    """
    b = _as_dict(bundle)
    proj = _as_str(b.get("project_name", ""))
    idea = _as_str(b.get("idea", ""))
    world = ensure_world(b.get("world"))
    characters = ensure_characters(b.get("characters"))
    tone = ensure_tone(b.get("tone"))
    outline = ensure_outline(b.get("outline"))

    chap_outline = pick_outline_for_chapter(b, int(chapter_index)) if chapter_index is not None else {}

    packed = {
        "project_name": proj,
        "idea": idea,
        "world": world,
        "characters": characters,
        "tone": tone,
        # outline 全量可能很大：只放 main_arc/themes + 当前章细纲
        "outline": {
            "main_arc": outline.get("main_arc", ""),
            "themes": outline.get("themes", []),
            "chapter": chap_outline,
        },
    }
    s = json.dumps(packed, ensure_ascii=False, indent=2)
    return truncate_text(s, max_chars=6500)


