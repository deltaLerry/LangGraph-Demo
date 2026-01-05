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

def default_materials_pack() -> Dict[str, Any]:
    """
    开题准备包（提纲挈领）：用于给 writer/editor 一个“全局指南”，而不是逐章细节。
    """
    return {
        "version": "pack_v1",
        "logline": "",
        "creative_brief": "",
        "pacing_plan": "",
        "arc_plan": [],
        "world_building": "",
        "growth_system": "",
        "style_guide": {"voice": "", "do": [], "dont": []},
        # 总编裁决层：用来“收敛口径”，防止各专家产出互相打架/过于专业导致不可用
        "conflicts_found": [],
        "decisions": [],
        "checklists": {"global": [], "per_arc": [], "per_chapter": []},
        "risks": [],
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
                    # 卷/副本结构（可选）：用于长篇“按Arc遗忘/下线”与节奏规划
                    "arc_id": _as_str(it.get("arc_id", "")),
                    "arc_title": _as_str(it.get("arc_title", "")),
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

def ensure_materials_pack(obj: Any) -> Dict[str, Any]:
    d = _as_dict(obj)
    out = default_materials_pack()
    out["version"] = _as_str(d.get("version", out["version"]))
    out["logline"] = _as_str(d.get("logline", ""))
    out["creative_brief"] = _as_str(d.get("creative_brief", ""))
    out["pacing_plan"] = _as_str(d.get("pacing_plan", ""))
    # arc_plan: list[dict]
    ap = d.get("arc_plan")
    out_ap: List[Dict[str, Any]] = []
    if isinstance(ap, list):
        for it in ap:
            if isinstance(it, dict):
                out_ap.append(
                    {
                        "arc_id": _as_str(it.get("arc_id", "")),
                        "arc_title": _as_str(it.get("arc_title", "")),
                        "start_chapter": int(it.get("start_chapter", 0) or 0),
                        "end_chapter": int(it.get("end_chapter", 0) or 0),
                        "purpose": _as_str(it.get("purpose", "")),
                        "stakes_escalation": _as_str(it.get("stakes_escalation", "")),
                        "ending_hook": _as_str(it.get("ending_hook", "")),
                    }
                )
    out["arc_plan"] = out_ap
    out["world_building"] = _as_str(d.get("world_building", ""))
    out["growth_system"] = _as_str(d.get("growth_system", ""))
    sg = _as_dict(d.get("style_guide"))
    out["style_guide"] = {
        "voice": _as_str(sg.get("voice", "")),
        "do": [str(x).strip() for x in _as_list(sg.get("do")) if str(x).strip()],
        "dont": [str(x).strip() for x in _as_list(sg.get("dont")) if str(x).strip()],
    }
    # conflicts_found: list[dict]
    cf = d.get("conflicts_found")
    out_cf: List[Dict[str, Any]] = []
    if isinstance(cf, list):
        for it in cf:
            if isinstance(it, dict):
                out_cf.append(
                    {
                        "topic": _as_str(it.get("topic", "")),
                        "evidence": _as_str(it.get("evidence", "")),
                        "impact": _as_str(it.get("impact", "")),
                    }
                )
    out["conflicts_found"] = out_cf
    # decisions: list[dict]
    ds = d.get("decisions")
    out_ds: List[Dict[str, Any]] = []
    if isinstance(ds, list):
        for it in ds:
            if isinstance(it, dict):
                out_ds.append(
                    {
                        "topic": _as_str(it.get("topic", "")),
                        "decision": _as_str(it.get("decision", "")),
                        "rationale": _as_str(it.get("rationale", "")),
                        "instructions": [str(x).strip() for x in _as_list(it.get("instructions")) if str(x).strip()],
                    }
                )
    out["decisions"] = out_ds
    cl = _as_dict(d.get("checklists"))
    out["checklists"] = {
        "global": [str(x).strip() for x in _as_list(cl.get("global")) if str(x).strip()],
        "per_arc": [str(x).strip() for x in _as_list(cl.get("per_arc")) if str(x).strip()],
        "per_chapter": [str(x).strip() for x in _as_list(cl.get("per_chapter")) if str(x).strip()],
    }
    rs = d.get("risks")
    out_rs: List[Dict[str, Any]] = []
    if isinstance(rs, list):
        for it in rs:
            if isinstance(it, dict):
                out_rs.append(
                    {
                        "risk": _as_str(it.get("risk", "")),
                        "symptom": _as_str(it.get("symptom", "")),
                        "mitigation": _as_str(it.get("mitigation", "")),
                    }
                )
    out["risks"] = out_rs
    return out


def build_materials_bundle(
    *,
    project_name: str,
    idea: str,
    world: Any,
    characters: Any,
    outline: Any,
    tone: Any,
    materials_pack: Any = None,
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
        "materials_pack": ensure_materials_pack(materials_pack),
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
    pack = ensure_materials_pack(b.get("materials_pack"))

    chap_outline = pick_outline_for_chapter(b, int(chapter_index)) if chapter_index is not None else {}

    packed = {
        "project_name": proj,
        "idea": idea,
        # 总编裁剪层（强约束/统一口径）：优先注入“裁决+关键约束”，避免多专家细节互相打架
        "materials_pack": {
            "logline": pack.get("logline", ""),
            "pacing_plan": pack.get("pacing_plan", ""),
            "style_guide": pack.get("style_guide", {}),
            "decisions": pack.get("decisions", [])[:8],
            "checklists": pack.get("checklists", {}),
        },
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


